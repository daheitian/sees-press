# -*- coding: utf-8 -*-
r"""PDF → EPUB 回归测试。不依赖 pytest / TeX：

  python tests/make_fixtures.py      # 首次生成夹具（也可直接提交生成好的 PDF）
  python tests/test_pdfextract.py

覆盖：双栏阅读顺序、页眉页码剔除、前置页剔除、章节重建、插图、脚注、
扫描件报错、字符保留率，以及从 PDF 一路构建到 EPUB 3 的包结构。
"""
from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sees_press.pdfextract import PDFError, extract_pdf      # noqa: E402
from sees_press.pipeline import BuildOptions, build           # noqa: E402

FIX = ROOT / "tests" / "fixtures"
BOOK = FIX / "双栏样书.pdf"
SCAN = FIX / "扫描件.pdf"
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILED
    print(("  OK   " if cond else "  FAIL ") + name + (("　" + detail) if detail else ""))
    if not cond:
        FAILED += 1


def ensure_fixtures() -> None:
    if BOOK.exists() and SCAN.exists():
        return
    import subprocess
    print("夹具缺失，尝试生成…")
    subprocess.run([sys.executable, str(ROOT / "tests" / "make_fixtures.py")], check=True)


def test_extract(tmp: Path) -> None:
    ex = extract_pdf(BOOK, assets_dir=tmp / "assets")
    d, md = ex.diagnostics, ex.markdown
    print("\n[1] 版面重建")
    check("识别出双栏", d["two_col_pages"] >= 3, f"{d['two_col_pages']} 页")
    check("剔除前置页与目录页", d["front_pages"] >= 3 and d["toc_pages"] >= 1,
          f"{d['front_pages']} / {d['toc_pages']}")
    check("剔除页眉与页码", d["dropped_headers"] >= 6, f"{d['dropped_headers']} 行")
    check("重建 3 章", d["chapters"] == 3 and len(re.findall(r"^## ", md, re.M)) == 3,
          f"诊断 {d['chapters']} 章")
    check("保留插图与脚注", d["images"] >= 1 and d["footnotes"] >= 1,
          f"{d['images']} 图 / {d['footnotes']} 注")
    check("字符零丢失", d["retention"] >= 0.97, f"{d['retention']:.1%}")
    check("无重建警告", not d["warnings"], "; ".join(d["warnings"]))
    check("段落已合并", "我们今天讨论纸张，其实是在讨论一种被忽略的时间。纸张不会主动说话，"
          "它只是把写字的人留下的犹豫与停顿一并收进纤维里。" in md)
    check("页眉不进正文", "见室译丛" not in md and "版权信息" not in md and "目 录" not in md)
    check("脚注引用与定义配对", "[^1]" in md and "[^1]:" in md)
    a = md.find("我们今天讨论纸张")
    b = md.find("排版是一种沉默的判断")
    c = md.find("译文里的一个逗号")
    check("双栏阅读顺序（左栏先于右栏）", 0 <= a < b < c, f"{a} / {b} / {c}")

    print("\n[2] 扫描件")
    try:
        extract_pdf(SCAN)
        check("无文本层应明确报错", False, "竟然没有抛错")
    except PDFError as e:
        check("无文本层应明确报错", True)
        check("报错里给出 OCR 建议", "OCR" in str(e))


def test_build(tmp: Path) -> None:
    print("\n[3] PDF → EPUB 3 全流程")
    out = tmp / "build"
    res = build(str(BOOK), BuildOptions(formats=("epub", "cover", "preview"),
                                        out_dir=str(out)))
    check("构建无阻断", res.ok,
          f"错误 {res.stats.get('errors')} / 警告 {res.stats.get('warnings')}")
    check("产出抽取稿", (out / "source.md").exists())
    report = (out / "report.md").read_text(encoding="utf-8")
    check("报告含 PDF 重建诊断", "PDF 重建诊断" in report and "内容保留率" in report)
    epubs = list(out.glob("*.epub"))
    check("产出 EPUB", bool(epubs))
    if not epubs:
        return
    with zipfile.ZipFile(epubs[0]) as z:
        names = z.namelist()
        first = z.infolist()[0]
        check("mimetype 包首且未压缩",
              first.filename == "mimetype"
              and first.compress_type == zipfile.ZIP_STORED
              and z.read("mimetype") == b"application/epub+zip")
        check("nav / opf / ncx 齐备",
              all(any(n.endswith(x) for n in names)
                  for x in ("nav.xhtml", "content.opf", "toc.ncx")))
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
        check("目录 3 章", len(re.findall(r"<li", nav)) >= 3)
        check("插图已打包", any("/images/fig" in n for n in names))
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        check("dc:creator 非空", bool(re.search(r"<dc:creator[^>]*>[^<]+</dc:creator>", opf)))


def main() -> int:
    ensure_fixtures()
    with tempfile.TemporaryDirectory(prefix="sees-press-pdf-") as t:
        tmp = Path(t)
        test_extract(tmp)
        test_build(tmp)
    print("\n" + ("全部通过" if not FAILED else f"{FAILED} 项未通过"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
