# -*- coding: utf-8 -*-
r"""质检层：把 Standard Ebooks 的 `se lint` / `se build --check` 思路落到本流水线。

分三道闸门：
  闸门 A  书稿层（构建前）：语义、图片、元数据、脚注、私有区残留
  闸门 B  产物层（构建后）：EPUB 包结构、XHTML 合法、链接/脚注配对、编译日志
  闸门 C  报告：汇总成一份可交付的 Markdown
外部已固化的 validate_epub.py（23 项）由 tools.py 调用后并入这里。
"""
from __future__ import annotations

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .model import Book, Issue
from .inline import FN_OPEN, FN_CLOSE, FN_RE

PRIVATE_RE = re.compile(r"[\ue000-\ue011\uf8ff]")
LATEX_RE = re.compile(r"\\(?!\\)[a-zA-Z]{2,}")


def gate_a(book: Book) -> list[Issue]:
    """构建前的书稿质检。"""
    out: list[Issue] = []

    def add(level, code, msg, where=""):
        out.append(Issue(level, code, msg, where))

    m = book.meta
    if not m.title or m.title == "未命名":
        add("warn", "书名", "使用占位书名「未命名」，建议显式指定", "书目")
    if not m.identifier:
        add("error", "标识符", "EPUB 缺少 dc:identifier", "书目")
    if not m.author:
        add("info", "作者", "未指定作者", "书目")

    slugs = [c.slug for c in book.chapters]
    if len(slugs) != len(set(slugs)):
        add("error", "文件名冲突", "章节 slug 重复，会互相覆盖", "目录")

    defined = {str(n.seq) for n in book.notes}
    used: set[str] = set()
    figure_paths: list[str] = []
    for i, c in enumerate(book.chapters, 1):
        where = f"第{i}章 {c.display_title}"
        for b in c.blocks:
            text = b.text + "\n".join(b.items)
            used |= set(FN_RE.findall(text))
            if PRIVATE_RE.search(FN_RE.sub("", text)):
                add("error", "私有区残留", "正文含未消费的控制 token，会印成怪字符", where)
            if LATEX_RE.search(text) and not b.kind == "code":
                add("warn", "疑似 LaTeX 残留", "正文出现反斜杠命令，确认不是误贴的源码", where)
            if b.kind == "figure":
                figure_paths.append(b.src)
                if not re.match(r"^(?:https?://|data:)", b.src, re.I) and not Path(b.src).exists():
                    add("warn", "图片缺失", f"找不到 {b.src}", where)
            if b.kind == "p" and len(b.text) > 1800:
                add("info", "超长段落", f"{len(b.text)} 字未分段，阅读器里会是一整堵墙", where)
    for k in sorted(used - defined):
        add("error", "脚注引用悬空", f"脚注 {k} 没有对应定义", "脚注")
    for k in sorted(defined - used):
        add("warn", "脚注未被引用", f"脚注 {k} 定义了却没在正文引用", "脚注")
    if book.word_count() < 80:
        add("warn", "内容过少", f"全文仅 {book.word_count()} 字，不足以成书", "全文")
    return out


def gate_b_epub(epub: str | os.PathLike) -> list[Issue]:
    """出包后的独立复核（不依赖外部工具，保证软件可独立分发）。"""
    out: list[Issue] = []

    def add(level, code, msg):
        out.append(Issue(level, code, msg, str(epub)))

    p = Path(epub)
    if not p.exists():
        add("error", "EPUB 未生成", "文件不存在")
        return out
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        infos = z.infolist()
        if not infos or infos[0].filename != "mimetype":
            add("error", "mimetype 位置", "必须位于 ZIP 包首")
        elif infos[0].compress_type != zipfile.ZIP_STORED:
            add("error", "mimetype 压缩", "必须 ZIP_STORED 未压缩")
        elif z.read("mimetype") != b"application/epub+zip":
            add("error", "mimetype 内容", "内容不正确")
        else:
            add("info", "mimetype", "包首 / 未压缩 / 内容正确")

        # container → OPF
        try:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = root.find(
                ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
            opf = ET.fromstring(z.read(opf_path))
        except Exception as e:
            add("error", "container.xml", f"无法解析 OPF：{e}")
            return out
        NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        for tag in ("identifier", "title", "language", "creator"):
            el = opf.find(f".//{{{NS['dc']}}}{tag}")
            if el is None or not (el.text or "").strip():
                add("error", "元数据缺失", f"dc:{tag} 为空")
        manifest = {it.get("id"): (it.get("href"), it.get("media-type"),
                                   it.get("properties") or "")
                    for it in opf.findall(".//opf:manifest/opf:item", NS)}
        spine = [x.get("idref") for x in opf.findall(".//opf:spine/opf:itemref", NS)]
        missing = [s for s in spine if s not in manifest]
        if missing:
            add("error", "spine 断链", f"idref 不在 manifest：{missing}")
        else:
            add("info", "spine", f"{len(spine)} 个 idref 全部对应 manifest")
        opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
        for href, _mt, _pr in manifest.values():
            full = f"{opf_dir}/{href}" if opf_dir else href
            if full not in names:
                add("error", "manifest 文件缺失", full)
        if not any("nav" in pr for _h, _m, pr in manifest.values()):
            add("error", "导航文档", "缺少 properties=nav")
        if not any("cover-image" in pr for _h, _m, pr in manifest.values()):
            add("error", "封面", "缺少 properties=cover-image")

        # XML 合法性 + 死链 + 脚注配对
        body = [n for n in names if re.match(r"OEBPS/(?:part|sec)\d+\.xhtml$", n)]
        xml_bad, dead, fn_bad, residue = [], [], [], []
        for n in names:
            if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                try:
                    ET.fromstring(z.read(n))
                except Exception as e:
                    xml_bad.append(f"{n}: {e}")
        for n in body:
            t = z.read(n).decode("utf-8", "replace")
            for href in re.findall(r'(?:href|src)="([^"#]+)', t):
                target = f"OEBPS/{href}" if not href.startswith(("OEBPS/", "http")) else href
                if target not in names and not href.startswith(("http", "data:")):
                    dead.append(f"{n} → {href}")
            refs = set(re.findall(r'href="#(fn\d+)"', t))
            defs = set(re.findall(r'id="(fn\d+)"', t))
            if refs != defs:
                fn_bad.append(n)
            if PRIVATE_RE.search(t) or LATEX_RE.search(t):
                residue.append(n)
        if xml_bad:
            add("error", "XML 非法", "; ".join(xml_bad[:3]))
        else:
            add("info", "XML", "全部 XHTML / OPF / NCX 可解析")
        if dead:
            add("error", "死链", "; ".join(dead[:3]))
        if fn_bad:
            add("error", "脚注不配对", "; ".join(fn_bad[:3]))
        if residue:
            add("error", "正文残留", f"未渲染标记：{residue[:3]}")
        add("info", "统计", f"{len(names)} 个条目 / {p.stat().st_size / 1024:.0f} KB / {len(body)} 节正文")
    return out


def gate_b_texlog(log_text: str) -> list[Issue]:
    out: list[Issue] = []
    overfull = re.findall(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", log_text)
    big = [float(x) for x in overfull if float(x) > 1.0]
    missing = re.findall(r"Missing character:.*?!([^ ]+)", log_text)
    if big:
        out.append(Issue("warn", "Overfull", f"{len(big)} 处 >1pt 溢出：{sorted(big, reverse=True)[:5]}", "xelatex"))
    else:
        out.append(Issue("info", "Overfull", f"0 处可见溢出（共 {len(overfull)} 处提示）", "xelatex"))
    if missing:
        out.append(Issue("warn", "缺字形", f"{len(missing)} 处：{' '.join(sorted(set(missing))[:8])}", "xelatex"))
    if re.search(r"^! ", log_text, re.M):
        msgs = re.findall(r"^! (.+)$", log_text, re.M)[:3]
        out.append(Issue("error", "编译错误", "; ".join(msgs), "xelatex"))
    return out


def render_report(book: Book, gates: dict[str, list[Issue]], artifacts: dict[str, str],
                  extra: list[str] | None = None) -> str:
    all_issues: list[Issue] = list(book.issues)
    for group in gates.values():
        all_issues.extend(group)
    errs = [i for i in all_issues if i.level == "error"]
    warns = [i for i in all_issues if i.level == "warn"]
    infos = [i for i in all_issues if i.level == "info"]

    m = book.meta
    lines = [
        f"# 印制质检报告 · {m.title}",
        "",
        f"- 版式：**{m.style}**　作者：{m.author or '（未指定）'}",
        f"- 规模：{book.stats.get('chapters', 0)} 章 / {book.stats.get('words', 0)} 字 / "
        f"{book.stats.get('notes', 0)} 条脚注 / {book.stats.get('figures', 0)} 张图",
        f"- 结论：**{'通过' if not errs else '存在阻断问题'}**　"
        f"错误 {len(errs)} · 警告 {len(warns)} · 提示 {len(infos)}",
        "",
        "## 产物",
        "",
    ]
    for k, v in artifacts.items():
        lines.append(f"- {k}：`{v}`")
    lines += ["", "## 质检明细", ""]
    for title, group in gates.items():
        lines.append(f"### {title}")
        lines.append("")
        if not group:
            lines.append("- 无问题")
        else:
            lines.extend(i.line() for i in group)
        lines.append("")
    if book.issues:
        lines.append("### 书稿解析")
        lines.append("")
        lines.extend(i.line() for i in book.issues)
        lines.append("")
    if extra:
        lines += ["## 外部校验器输出", "", "```text"]
        lines.extend(x.rstrip() for x in extra)
        lines += ["```", ""]
    lines += [
        "---",
        "",
        "由 见室印制所 SEES PRESS 自动生成。EPUB 结构校验同时执行内置复核与 "
        "publisher-typography 的 23 项校验脚本；PDF 验收以 xelatex 日志为准。",
        "",
    ]
    return "\n".join(lines)
