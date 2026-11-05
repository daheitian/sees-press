# -*- coding: utf-8 -*-
"""命令行入口。

  python -m sees_press.cli build 稿件.md --title "书名" --author "作者" --style 疏朗留白
  python -m sees_press.cli build 扫描书.pdf --formats epub,cover,preview
  python -m sees_press.cli pdf2md 书.pdf -o 抽取稿.md       # 只重建书稿，人工校对
  python -m sees_press.cli pdf2md 书.pdf --check             # 只体检，不落盘
  python -m sees_press.cli compat 成品.epub                  # 上架兼容体检
  python -m sees_press.cli build https://example.com/essay --formats epub,pdf
  python -m sees_press.cli styles
  python -m sees_press.cli doctor
  python -m sees_press.cli demo
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import build, BuildOptions
from .styles import STYLES


def _opt_from_args(a) -> BuildOptions:
    keys = ("title", "subtitle", "author", "region", "translator", "publisher",
            "editor", "edition", "isbn", "identifier", "desc", "subject",
            "language", "rights", "series", "style", "quote_style",
            "cjk_spacing", "chapter_newpage", "keep_pdf_headers",
            "cover", "cover_ratio",
            "out_dir", "use_legacy_validator", "emit_body", "emit_ir")
    kw = {k: getattr(a, k) for k in keys if hasattr(a, k)}
    kw["formats"] = tuple(x.strip() for x in a.formats.split(",") if x.strip())
    return BuildOptions(**kw)


def cmd_build(a) -> int:
    opt = _opt_from_args(a)
    res = build(a.source, opt, progress=lambda m: print("  " + m, flush=True))
    print("\n" + "=" * 58)
    print(f"  见室印制所 · {'完成' if res.ok else '有阻断项'}")
    print("=" * 58)
    for k, v in res.artifacts.items():
        print(f"  {k:<18} {v}")
    print(f"\n  错误 {res.stats.get('errors', 0)} / 警告 {res.stats.get('warnings', 0)}")
    print(f"  工作目录：{res.workdir}")
    if not res.ok:
        for i in res.issues:
            if i.level == "error":
                print(f"  [✗] {i.code}: {i.msg}")
    return 0 if res.ok else 1


def cmd_pdf2md(a) -> int:
    from .pdfextract import PDFError, diagnostics_markdown, extract_pdf
    src = Path(a.source)
    out = Path(a.out) if a.out else src.with_suffix(".md")
    try:
        ex = extract_pdf(src, assets_dir=out.parent / "pdf_assets")
    except PDFError as e:
        print("PDF 处理失败：" + str(e))
        return 1
    if a.check:
        print(diagnostics_markdown(ex.diagnostics))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ex.markdown, encoding="utf-8")
    d = ex.diagnostics
    print(f"已重建书稿：{out}")
    print(f"  {d['pages']} 页 → {d['chapters']} 章 / {d['paragraphs']} 段，"
          f"字符保留率 {d['retention']:.0%}")
    print(f"  剔除前置页 {d['front_pages']} 页、页眉页码 {d['dropped_headers']} 行；"
          f"插图 {d['images']} 张、脚注 {d['footnotes']} 条")
    for w in d["warnings"]:
        print("  [!] " + w)
    print("  校对后可直接投回：python -m sees_press.cli build " + str(out))
    return 0


def cmd_compat(a) -> int:
    from .compat import audit_epub, render_compat_report
    au = audit_epub(a.epub)
    print(f"  {au.verdict}　（{len(au.passed)} 通过 / {len(au.warns)} 建议 / {len(au.errors)} 必须修）")
    for f in au.findings:
        if f.level != "ok":
            mark = "✗" if f.level == "error" else "!"
            print(f"  [{mark}] {f.code} · {f.title}：{f.detail}")
    dest = Path(a.output) if a.output else Path(a.epub).with_suffix(".compat.md")
    dest.write_text(render_compat_report(au), encoding="utf-8")
    print(f"  报告：{dest}")
    return 1 if au.errors else 0


def cmd_styles(_a) -> int:
    print(f"{'版式':<10}{'开本 mm':<14}{'行距':<7}{'专色':<10}气质")
    print("-" * 72)
    for name, s in STYLES.items():
        print(f"{name:<10}{str(s['paper'][0]) + '×' + str(s['paper'][1]):<14}"
              f"{s['leading']:<7}{s['accent']:<10}{s['desc']}")
    return 0


def cmd_doctor(_a) -> int:
    from . import tools
    print("环境自检")
    print("-" * 58)
    for k, v in tools.environment_report().items():
        print(f"  {k:<30} {v}")
    return 0


def cmd_demo(a) -> int:
    demo = Path(__file__).resolve().parent.parent / "examples" / "demo.md"
    if not demo.exists():
        print("找不到 examples/demo.md")
        return 1
    opt = BuildOptions(style=a.style or "疏朗留白", out_dir=a.out_dir or "")
    res = build(str(demo), opt, progress=lambda m: print("  " + m, flush=True))
    print(f"\n产物目录：{res.workdir}")
    for k, v in res.artifacts.items():
        print(f"  {k:<18} {v}")
    return 0 if res.ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sees-press", description="见室印制所 · 输入内容，自动成书")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="把文稿或 PDF 做成电子书")
    b.add_argument("source", help="正文 / .pdf .md .txt .docx .html 路径 / 目录 / URL")
    for k in ("title", "subtitle", "author", "region", "translator", "publisher",
              "editor", "edition", "isbn", "identifier", "desc", "subject", "series", "cover"):
        b.add_argument("--" + k.replace("_", "-"), default="")
    b.add_argument("--style", default="", help="版式名，见 `styles`；留空则读稿件 frontmatter")
    b.add_argument("--quote-style", default="auto", choices=["auto", "corner", "curly", "straight"])
    b.add_argument("--formats", default="epub,pdf,cover,preview")
    b.add_argument("--out-dir", default="")
    b.add_argument("--language", default="zh-CN")
    b.add_argument("--rights", default="")
    b.add_argument("--cover-ratio", default="2:3", choices=["2:3", "3:4"])
    b.add_argument("--keep-pdf-headers", action="store_true",
                   help="PDF 输入时保留页眉页脚（默认剔除）")
    b.add_argument("--no-cjk-spacing", action="store_false", dest="cjk_spacing")
    b.add_argument("--no-chapter-newpage", action="store_false", dest="chapter_newpage")
    b.add_argument("--no-legacy-validator", action="store_false", dest="use_legacy_validator")
    b.add_argument("--no-body", action="store_false", dest="emit_body")
    b.add_argument("--no-ir", action="store_false", dest="emit_ir")
    b.set_defaults(func=cmd_build, cjk_spacing=True, chapter_newpage=True,
                   keep_pdf_headers=False, use_legacy_validator=True,
                   emit_body=True, emit_ir=True)

    pm = sub.add_parser("pdf2md", help="把 PDF 重建为可校对的结构化书稿")
    pm.add_argument("source", help="PDF 文件路径")
    pm.add_argument("-o", "--out", default="", help="输出 .md 路径（默认与 PDF 同名）")
    pm.add_argument("--check", action="store_true", help="只输出体检结果，不生成文件")
    pm.set_defaults(func=cmd_pdf2md)

    c = sub.add_parser("compat", help="EPUB 上架兼容体检（微信读书等平台通用准入线）")
    c.add_argument("epub", help="要检查的 .epub 文件")
    c.add_argument("-o", "--output", help="报告输出路径（默认同目录 .compat.md）")
    c.set_defaults(func=cmd_compat)

    sub.add_parser("styles", help="列出七种版式").set_defaults(func=cmd_styles)
    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)
    d = sub.add_parser("demo", help="用示例文稿跑一遍全流程")
    d.add_argument("--style", default="疏朗留白")
    d.add_argument("--out-dir", default="")
    d.set_defaults(func=cmd_demo)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
