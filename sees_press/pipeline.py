# -*- coding: utf-8 -*-
r"""编排层：输入内容 → 一份 IR → 全部产物 + 质检报告。

  输入归一化 → 排版净化 → 结构解析（IR）
      ├─→ body.tex  ──→ book.tex ──→ XeLaTeX ×2 ──→ PDF（含书签注入）
      ├─→ 语义化 XHTML ──→ EPUB 3 ──→ 双重校验（内置 + 既有 23 项）
      ├─→ 程序化封面 PNG（PDF 与 EPUB 共用同一张，标题已烤入）
      ├─→ 单文件 HTML 预览（recompose-epub）
      └─→ report.md（三道闸门汇总）

任何一条支线失败都不会拖垮其余支线：软件逐条记录并继续，最后如实报告。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .model import Meta, Book, Issue
from .ingest import read_source
from .parse import parse_book
from . import lint, tools
from .cover import make_cover
from .epub import build_epub
from .preview import build_preview
from .tex import render_body, render_book_tex

DEFAULT_FORMATS = ("epub", "pdf", "cover", "preview")


@dataclass
class BuildOptions:
    # 书目
    title: str = ""
    subtitle: str = ""
    author: str = ""
    region: str = ""
    translator: str = ""
    publisher: str = ""
    editor: str = ""
    edition: str = ""
    isbn: str = ""
    identifier: str = ""
    desc: str = ""
    subject: str = ""
    language: str = "zh-CN"
    rights: str = ""
    series: str = ""
    # 排版
    style: str = ""                    # 空 = 尊重稿件 frontmatter，否则默认疏朗留白
    quote_style: str = "auto"          # auto | corner | curly | straight
    cjk_spacing: bool = True           # EPUB 中西文细空格
    chapter_newpage: bool = True       # PDF 每章起新页
    keep_pdf_headers: bool = False     # PDF 输入时保留页眉页脚（默认剔除）
    cover: str = ""                    # 自带封面图，接管程序化封面
    cover_ratio: str = "2:3"
    # 产出
    formats: tuple[str, ...] = DEFAULT_FORMATS
    out_dir: str = ""
    use_legacy_validator: bool = True
    emit_body: bool = True             # 额外输出 body.tex（可接回旧流程）
    emit_ir: bool = True

    def meta(self) -> Meta:
        return Meta(**{k: getattr(self, k) for k in (
            "title", "subtitle", "author", "region", "translator", "publisher",
            "editor", "edition", "isbn", "identifier", "desc", "subject",
            "language", "rights", "series")},
            style=self.style or "疏朗留白")


@dataclass
class BuildResult:
    ok: bool = True
    workdir: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    report: str = ""
    title: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["report"] = ""       # 报告单独读文件，别塞进 JSON
        return d


def _safe(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name).strip(" .")
    return (s or "book")[:60]


def build(source: str, options: BuildOptions | None = None,
          progress=None) -> BuildResult:
    """主入口。source = 正文 / 文件路径 / 目录 / URL。"""
    opt = options or BuildOptions()
    res = BuildResult()

    def emit(msg: str):
        res.logs.append(msg)
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    # 产物目录
    out = Path(opt.out_dir).resolve() if opt.out_dir else (
        Path(__file__).resolve().parent.parent / "out" /
        f"{time.strftime('%Y%m%d-%H%M%S')}-{_safe(opt.title or 'book')}")
    out.mkdir(parents=True, exist_ok=True)
    res.workdir = str(out)

    try:
        # ── 1. 输入 ──────────────────────────────────────────────
        emit("读取输入…")
        src_str = str(source)
        src_path = Path(src_str) if len(src_str) < 500 else None
        is_pdf = bool(src_path and src_path.is_file()
                      and src_path.suffix.lower() == ".pdf")
        pdf_diag: dict = {}
        if is_pdf:
            # PDF 只是素材入口：先重建语义书稿，再由同一份 IR 派生所有出口。
            # 抽取稿单独落盘，便于人工校对后改投 source.md 重跑。
            from .pdfextract import extract_pdf
            ex = extract_pdf(src_path, assets_dir=out / "pdf_assets",
                             title=opt.title, author=opt.author,
                             keep_headers=opt.keep_pdf_headers)
            raw, name = ex.markdown, src_path.name
            pdf_diag = ex.diagnostics
            (out / "source.md").write_text(raw, encoding="utf-8")
            res.artifacts["PDF 抽取稿"] = str(out / "source.md")
            emit(f"PDF 重建：{pdf_diag['pages']} 页 → {pdf_diag['chapters']} 章 / "
                 f"{pdf_diag['paragraphs']} 段，剔除前置页 {pdf_diag['front_pages']} 页 + "
                 f"页眉页码 {pdf_diag['dropped_headers']} 行，"
                 f"字符保留率 {pdf_diag['retention']:.0%}")
            base_dir = out
        else:
            raw, name = read_source(source, base=out.parent)
            emit(f"来源：{name}　{len(raw):,} 字符")
            base_dir = src_path.parent if src_path and src_path.is_file() else None
        # 版式优先级：命令行/GUI 显式指定 > 稿件 frontmatter > 默认
        from .ingest import split_frontmatter
        from .styles import STYLES, DEFAULT_STYLE
        fm, _ = split_frontmatter(raw)
        chosen = opt.style or fm.get("style") or DEFAULT_STYLE
        if chosen not in STYLES:
            chosen = DEFAULT_STYLE
        meta = opt.meta()
        meta.style = chosen
        book = parse_book(raw, meta, quote_style=opt.quote_style,
                          base_dir=base_dir, source_name=name)
        res.title = book.meta.title
        emit(f"解析完成：{book.stats['chapters']} 章 / {book.stats['words']:,} 字 / "
             f"{book.stats['notes']} 脚注 / {book.stats['figures']} 图")

        # ── 2. 闸门 A ────────────────────────────────────────────
        gates: dict[str, list[Issue]] = {"闸门 A · 书稿": lint.gate_a(book)}
        if pdf_diag:
            gates["闸门 A · PDF 重建"] = [
                Issue("warn", "PDF 重建", w) for w in pdf_diag.get("warnings", [])
            ]
        blockers = [i for i in gates["闸门 A · 书稿"] if i.level == "error"]
        emit(f"书稿质检：{len(gates['闸门 A · 书稿'])} 条（阻断 {len(blockers)}）")

        # ── 3. 正文源（兼容旧流程）────────────────────────────────
        # 两条管线各一份正文源：body.tex 守旧契约，book_body.tex 供 LaTeX
        (out / "body.tex").write_text(render_body(book, for_pdf=False), encoding="utf-8")
        (out / "book_body.tex").write_text(render_body(book, for_pdf=True), encoding="utf-8")
        if opt.emit_body:
            res.artifacts["正文源 body.tex"] = str(out / "body.tex")
            res.artifacts["PDF 正文源"] = str(out / "book_body.tex")

        # ── 4. 封面 ──────────────────────────────────────────────
        cover_path = out / "cover.png"
        if opt.cover and Path(opt.cover).exists():
            shutil.copy2(opt.cover, cover_path)
            emit("使用自带封面图（标题需自行烤入）")
        else:
            emit("生成编辑印刷风封面…")
            make_cover(book.meta, cover_path, ratio=opt.cover_ratio)
        res.artifacts["封面"] = str(cover_path)

        # ── 5. EPUB ──────────────────────────────────────────────
        epub_path = out / (_safe(book.meta.title) + ".epub")
        if "epub" in opt.formats:
            emit("生成语义化 EPUB 3…")
            build_epub(book, epub_path, cover=cover_path, work=out,
                       cjk_spacing=opt.cjk_spacing)
            res.artifacts["EPUB 3"] = str(epub_path)
            gates["闸门 B · EPUB 包（内置）"] = lint.gate_b_epub(epub_path)
            extras: list[str] = []
            if opt.use_legacy_validator:
                legacy = tools.validate_epub_legacy(epub_path)
                if legacy:
                    extras.append(legacy)
                    emit("已执行 publisher-typography 的 23 项校验")
            if extras:
                (out / "validate_epub.txt").write_text("\n".join(extras), encoding="utf-8")
                res.artifacts["校验记录"] = str(out / "validate_epub.txt")
            emit("EPUB 完成")

        # ── 6. PDF ───────────────────────────────────────────────
        if "pdf" in opt.formats:
            emit("生成 XeLaTeX 整书模板…")
            (out / "book.tex").write_text(
                render_book_tex(book, cover="cover.png", body_name="book_body",
                                chapter_newpage=opt.chapter_newpage), encoding="utf-8")
            res.artifacts["整书模板 book.tex"] = str(out / "book.tex")
            # 插图集中到 figs/，供 \graphicspath 与旧 tex_to_epub.py 共用
            figs = out / "figs"
            figs.mkdir(exist_ok=True)
            for c in book.chapters:
                for b in c.blocks:
                    if b.kind == "figure" and Path(b.src).exists():
                        shutil.copy2(b.src, figs / Path(b.src).name)
            emit("编译 PDF（xelatex ×2）…")
            pdf, log = tools.compile_pdf(out, "book.tex", passes=2)
            gates["闸门 B · PDF 编译日志"] = lint.gate_b_texlog(log)
            if pdf:
                dest = out / (_safe(book.meta.title) + ".pdf")
                if pdf != dest:
                    shutil.copy2(pdf, dest)
                emit(tools.inject_outline(dest, book))
                res.artifacts["PDF"] = str(dest)
                emit("PDF 完成")
            else:
                emit("PDF 未生成：" + (log.splitlines()[0] if log else "xelatex 无输出"))
                res.artifacts["PDF"] = ""
                (out / "xelatex.log").write_text(log, encoding="utf-8")

        # ── 7. 预览 ──────────────────────────────────────────────
        if "preview" in opt.formats and epub_path.exists():
            emit("重组单文件网页预览…")
            pv = out / "preview.html"
            build_preview(epub_path, pv, title=book.meta.title)
            res.artifacts["网页预览"] = str(pv)

        # ── 8. IR 快照 ───────────────────────────────────────────
        if opt.emit_ir:
            p = out / "book.json"
            p.write_text(json.dumps(book.to_dict(), ensure_ascii=False, indent=2),
                         encoding="utf-8")
            res.artifacts["书稿 IR"] = str(p)

        # ── 9. 报告 ──────────────────────────────────────────────
        extras_out = []
        f = out / "validate_epub.txt"
        if f.exists():
            extras_out = [f.read_text(encoding="utf-8")]
        report = lint.render_report(book, gates,
                                    {k: v for k, v in res.artifacts.items() if v},
                                    extra=extras_out)
        if pdf_diag:
            from .pdfextract import diagnostics_markdown
            block = diagnostics_markdown(pdf_diag)
            report = report.replace("\n---\n", "\n" + block + "\n\n---\n", 1)
        (out / "report.md").write_text(report, encoding="utf-8")
        res.artifacts["质检报告"] = str(out / "report.md")
        res.report = report

        res.artifacts = {k: v for k, v in res.artifacts.items() if v}
        res.issues = book.issues + [i for g in gates.values() for i in g]
        res.stats = {**book.stats, "errors": sum(1 for i in res.issues if i.level == "error"),
                     "warnings": sum(1 for i in res.issues if i.level == "warn"),
                     "environment": tools.environment_report()}
        res.ok = not any(i.level == "error" for i in res.issues)
        emit(f"完成：{'通过' if res.ok else '存在阻断项'}，"
             f"错误 {res.stats['errors']} / 警告 {res.stats['warnings']}")
    except Exception as e:
        res.ok = False
        tb = traceback.format_exc()
        emit(f"构建失败：{e}")
        res.issues.append(Issue("error", "构建异常", str(e)))
        (out / "error.log").write_text(tb, encoding="utf-8")
        res.artifacts["错误日志"] = str(out / "error.log")
    return res


def _style_names():
    from .styles import STYLES
    return set(STYLES)
