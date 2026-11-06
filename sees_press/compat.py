# -*- coding: utf-8 -*-
r"""EPUB 上架兼容体检（纯标准库 + Pillow，不依赖 Java / EPUBCheck）。

定位：官方 EPUBCheck 检查「规范是否合法」，这里检查「国产阅读器里好不好读、
主流平台肯不肯收」。两类问题各不相同，后者常是上架被退回的真正原因。

分级：
  error  会被平台拒收，或阅读器里明显错乱 —— 必须修
  warn   能打开但体验受损 / 部分平台会挑 —— 建议修
  ok     通过

检查域：包结构 · 元数据 · 封面 · CSS 兼容 · 重排能力 · 资源引用 · 语义结构。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote
import xml.etree.ElementTree as ET

MAX_IMAGE_PIXELS = 3_200_000      # 主流平台单图上限（Apple 400 万，取更严的 320 万）
COVER_MIN_SHORT = 1400            # Apple Books 封面短边下限
OPF_NS = "{http://www.idpf.org/2007/opf}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"

CSS_RISKS = [
    (r"var\s*\(", "CSS 自定义属性", "旧版 WebView 不支持，含它的整条声明会被丢弃"),
    (r"#[0-9a-fA-F]{8}\b", "8 位 hex 颜色", "旧版 WebView 不认，背景/边框会失效"),
    (r"text-size-adjust\s*:\s*none", "锁死字号", "用户无法调整字号，违反可缩放要求"),
    (r"list-style\s*:\s*[\"']", "字符串型 list-style", "支持面极窄，列表符号会消失"),
    (r"position\s*:\s*(absolute|fixed)", "绝对定位", "流式 EPUB 禁止，重排时会错位"),
    (r"\)\s*[0-9a-fA-F]{2}\s*;", "变量拼接色值", "无效 CSS，整条声明被丢弃"),
]


@dataclass
class Finding:
    level: str                  # error | warn | ok
    code: str
    title: str
    detail: str = ""


@dataclass
class Audit:
    path: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, code: str, title: str, detail: str = "") -> None:
        self.findings.append(Finding(level, code, title, detail))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def passed(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "ok"]

    @property
    def verdict(self) -> str:
        if self.errors:
            return f"不达标：{len(self.errors)} 项必须修"
        if self.warns:
            return f"可用，{len(self.warns)} 项建议优化"
        return "达标"


def _text(z: zipfile.ZipFile, name: str) -> str:
    try:
        return z.read(name).decode("utf-8", "replace")
    except KeyError:
        return ""


def audit_epub(path: str | Path) -> Audit:
    p = Path(path)
    a = Audit(path=str(p))
    if not p.exists():
        a.add("error", "文件", "EPUB 不存在", str(p))
        return a

    mb = p.stat().st_size / 1048576
    if mb > 100:
        a.add("error", "体积", "超过 100 MB 上架上限", f"{mb:.1f} MB")
    elif mb > 50:
        a.add("warn", "体积", "超过建议的 50 MB", f"{mb:.1f} MB")
    else:
        a.add("ok", "体积", "文件大小合规", f"{mb:.2f} MB")

    try:
        z = zipfile.ZipFile(p)
    except Exception as e:
        a.add("error", "ZIP", "无法解包", type(e).__name__)
        return a

    names = z.namelist()
    infos = z.infolist()

    # ── 包结构 ────────────────────────────────────────────────
    if not names or names[0] != "mimetype":
        a.add("error", "mimetype", "mimetype 不是包内第一个条目")
    else:
        bad = []
        if infos[0].compress_type != zipfile.ZIP_STORED:
            bad.append("被压缩了")
        raw = z.read("mimetype")
        if raw != b"application/epub+zip":
            bad.append(f"内容异常 {raw[:40]!r}")
        a.add("error" if bad else "ok", "mimetype", "包首 · STORED · 内容严格",
              "；".join(bad) or "application/epub+zip（无 BOM / 换行）")

    container = _text(z, "META-INF/container.xml")
    a.add("ok" if "content.opf" in container or "rootfile" in container else "error", "container",
          "container.xml 指向 OPF", "META-INF/container.xml")

    # OPF 路径：优先读 container.xml（兼容单引号 / 非标准路径），
    # 退化时再退回「包内唯一的 .opf」
    try:
        from .epubread import opf_path
        opf_name = opf_path(z)
    except Exception:
        opf_name = next((n for n in names if n.lower().endswith(".opf")), "")
    opf = _text(z, opf_name) if opf_name else ""
    if not opf:
        a.add("error", "OPF", "找不到 content.opf")
        return a
    a.add("ok" if 'version="3.0"' in opf else "warn", "OPF", "EPUB 3.0 声明",
          'version="3.0"' if 'version="3.0"' in opf else "非 3.0，部分平台只收 EPUB 3")

    # ── 元数据 ────────────────────────────────────────────────
    try:
        root = ET.fromstring(opf)
        md = root.find(OPF_NS + "metadata")
        def dc(tag: str) -> str:
            el = md.find(DC_NS + tag) if md is not None else None
            return (el.text or "").strip() if el is not None else ""
        title, creator, lang, ident = dc("title"), dc("creator"), dc("language"), dc("identifier")
    except Exception as e:
        a.add("error", "OPF", "XML 解析失败", type(e).__name__)
        title = creator = lang = ident = ""
    missing = [k for k, v in (("书名", title), ("作者", creator),
                              ("语言", lang), ("唯一标识", ident)) if not v]
    a.add("error" if missing else "ok", "元数据", "dc:title / creator / language / identifier",
          "缺：" + "、".join(missing) if missing else f"{title} · {creator} · {lang}")
    if not re.search(r"dcterms:modified", opf):
        a.add("warn", "元数据", "缺 dcterms:modified", "EPUB 3 要求")
    if "rendition:layout" in opf and "reflowable" in opf:
        a.add("ok", "流版", "显式声明 reflowable")
    elif "rendition:layout" not in opf:
        a.add("warn", "流版", "未显式声明 reflowable", "默认即流式，但显式声明更稳")
    else:
        a.add("error", "流版", "不是流式排版", "上架平台普遍只收 reflowable")

    # ── 目录 / spine ──────────────────────────────────────────
    has_nav = any(n.endswith("nav.xhtml") for n in names)
    has_ncx = any(n.endswith(".ncx") for n in names)
    a.add("ok" if has_nav and has_ncx else "error", "双目录",
          "nav.xhtml + toc.ncx", f"nav={has_nav} ncx={has_ncx}")
    if has_nav and 'properties="nav"' not in opf and "properties='nav'" not in opf:
        a.add("error", "双目录", "nav.xhtml 未声明 properties=\"nav\"")
    if has_ncx and "application/x-dtbncx+xml" not in opf:
        a.add("error", "双目录", "toc.ncx 的 media-type 错误")

    item_ids = set(re.findall(r'<item\s[^>]*id="([^"]+)"', opf))
    refs = re.findall(r'<itemref\s[^>]*idref="([^"]+)"', opf)
    dangling = [r for r in refs if r not in item_ids]
    a.add("error" if dangling else "ok", "spine",
          "idref 全部能在 manifest 找到", "孤立：" + ", ".join(dangling[:5]) if dangling
          else f"{len(refs)} 个阅读顺序条目")

    # ── 封面 ──────────────────────────────────────────────────
    # 四层回退找封面（见 epubread.extract_cover）：cover-image 属性 /
    # meta name=cover（id 或文件名）/ guide 首图 / 兜底 image+cover 字样。
    try:
        from .epubread import extract_cover
        cname = extract_cover(z, opf_name)
    except Exception:
        cname = None
    if not cname:
        a.add("error", "封面", "OPF 未声明可读的封面图",
              "需 cover-image 属性 / meta name=cover / guide reference")
    elif cname not in names:
        a.add("error", "封面", "声明的封面文件不在包内", cname)
    else:
        data = z.read(cname)
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            w, h = im.size
            px = w * h
            short = min(w, h)
            fmt, mode = im.format, im.mode
            issues = []
            if fmt != "JPEG":
                issues.append(f"{fmt} 格式（建议 JPEG）")
            if mode not in ("RGB", "L"):
                issues.append(f"{mode} 模式（需 RGB）")
            if px > MAX_IMAGE_PIXELS:
                issues.append(f"{px:,} 像素 > {MAX_IMAGE_PIXELS:,} 上限")
            if short < COVER_MIN_SHORT:
                issues.append(f"短边 {short}px < {COVER_MIN_SHORT}px")
            a.add("error" if any("像素" in x for x in issues)
                  else ("warn" if issues else "ok"),
                  "封面", "JPEG / RGB / ≤320 万像素 / 短边 ≥1400",
                  "；".join(issues) or f"{fmt} {mode} {w}×{h}，{px:,} 像素")
        except Exception as e:
            a.add("error", "封面", "图片无法解析", type(e).__name__)

    # 单图像素上限（不只是封面）
    try:
        from PIL import Image
        for n in names:
            if not re.search(r"\.(jpe?g|png|gif|webp)$", n, re.I):
                continue
            try:
                im = Image.open(io.BytesIO(z.read(n)))
                if im.size[0] * im.size[1] > MAX_IMAGE_PIXELS:
                    a.add("error", "插图", "单图超过 320 万像素", f"{n} {im.size}")
            except Exception:
                pass
    except ImportError:
        a.add("warn", "插图", "未安装 Pillow，跳过图片像素检查")

    # ── CSS 兼容性 ────────────────────────────────────────────
    css_name = next((n for n in names if n.endswith(".css")), "")
    css = _text(z, css_name)
    if not css:
        a.add("warn", "CSS", "包内没有样式表", "排版会完全交给阅读器默认样式")
    else:
        hits = []
        for pat, label, why in CSS_RISKS:
            if re.search(pat, css):
                hits.append(f"{label}（{why}）")
        a.add("error" if hits else "ok", "CSS 兼容",
              "无旧内核不认的语法", "；".join(hits) or "变量 / 8 位 hex / 字号锁定 / 绝对定位全部规避")
        if "page-break-before: always" not in css:
            a.add("warn", "CSS 兼容", "章节未双写 page-break-before", "旧内核只认 page-break-* ")
        if re.search(r"body\s*\{[^}]*font-size\s*:\s*\d+px", css, re.S):
            a.add("error", "可缩放", "正文字号用 px 写死", "用户改字号无效")
        else:
            a.add("ok", "可缩放", "正文字号用相对单位", "em / %，随阅读器设置缩放")
        if "font-family: serif" in css or "font-family:serif" in css:
            a.add("ok", "可缩放", "中文字体交还阅读器", "不硬指定平台专属字体")
        if "table" in css and "border-collapse" in css:
            a.add("ok", "表格", "提供真表格样式")

    # ── 资源引用 / 路径 ───────────────────────────────────────
    non_ascii = [n for n in names if not n.isascii()]
    a.add("error" if non_ascii else "ok", "路径",
          "包内文件名全为 ASCII", "非 ASCII：" + ", ".join(non_ascii[:3]) if non_ascii
          else f"{len(names)} 个条目")

    xhtmls = [n for n in names if n.endswith((".xhtml", ".html"))]
    broken: list[str] = []
    for n in xhtmls:
        doc = _text(z, n)
        for attr in re.findall(r'(?:src|href)="([^"]+)"', doc):
            if attr.startswith(("http:", "https:", "data:", "mailto:", "#")):
                continue
            target = unquote(attr.split("#")[0])
            if not target:
                continue
            base = n.rsplit("/", 1)[0] + "/" if "/" in n else ""
            full = (base + target).replace("/./", "/")
            while "/../" in full:
                full = re.sub(r"[^/]+/\.\./", "", full, count=1)
            if full not in names:
                broken.append(f"{n} → {attr}")
    a.add("error" if broken else "ok", "资源引用",
          "XHTML 引用的图片 / 链接全部存在", "死链：" + "; ".join(broken[:3]) if broken
          else f"{len(xhtmls)} 个 XHTML 校验完毕")

    # ── 语义结构 ──────────────────────────────────────────────
    chapters = [n for n in names if re.search(r"/(?:sec|part|ch)\d+\.xhtml$", n)]
    a.add("ok" if chapters else "warn", "章节",
          "每章独立 XHTML", f"{len(chapters)} 个章节文件")
    body_docs = "".join(_text(z, n) for n in chapters[:40])
    if 'epub:type="footnote"' in body_docs:
        back = body_docs.count('class="backref"')
        a.add("ok" if back else "warn", "脚注",
              "语义脚注带返回链接", f"{body_docs.count('epub:type=\"footnote\"')} 条注释 / {back} 个返回链接")
    pre_fake = body_docs.count("<pre")
    if pre_fake > 20:
        a.add("warn", "表格", "疑似用 <pre> 假表格", f"{pre_fake} 个 pre 块，建议改真 <table>")
    elif "<table" in body_docs:
        a.add("ok", "表格", "使用语义 <table>", f"{body_docs.count('<table')} 个表格")
    langs = [n for n in xhtmls if 'xml:lang=' in _text(z, n)]
    a.add("ok" if len(langs) == len(xhtmls) else "warn", "语言",
          "XHTML 声明 xml:lang", f"{len(langs)}/{len(xhtmls)}")

    z.close()
    return a


def render_compat_report(a: Audit) -> str:
    icon = {"error": "✗", "warn": "!", "ok": "✓"}
    label = {"error": "必须修", "warn": "建议", "ok": "通过"}
    lines = [
        f"# EPUB 上架兼容体检 · {Path(a.path).name}",
        "",
        f"**结论：{a.verdict}**　（{len(a.passed)} 通过 / {len(a.warns)} 建议 / {len(a.errors)} 必须修）",
        "",
        "对照：EPUB 3 流版规范 + EPUBCheck 常见拒收项 + 微信读书等国产阅读器兼容实践。",
        "官方 EPUBCheck 另需单独跑（本机无 Java），本表查的是「好不好读、平台肯不肯收」。",
        "",
        "| | 项目 | 判定 | 说明 |",
        "|---|---|---|---|",
    ]
    for f in a.findings:
        detail = (f.detail or "—").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {icon[f.level]} | {f.code} · {f.title} | {label[f.level]} | {detail} |")
    lines += [
        "",
        "## 上架说明（现实路径）",
        "",
        "微信读书没有对外公开的「个人上传 EPUB 即上架」技术文档。个人原创作品走",
        "微信读书创作平台 / 作家助手投稿，机构与出版社走阅文内容合作（需出版资质、",
        "三审三校与完整版权链）。技术文件在审核通过后由平台按出版规范处理。",
        "因此本表按**主流平台通用准入线**设定：EPUB 3 流版、EPUBCheck 零错误、",
        "封面 JPEG/RGB/≤320 万像素/短边 ≥1400、双目录、元数据完整、可缩放、",
        "资源全 ASCII 且无死链。达到这一线，导入微信读书阅读与交付平台方都不会返工。",
        "",
    ]
    return "\n".join(lines)
