# -*- coding: utf-8 -*-
r"""EPUB 生成与读取的回归测试（不依赖 pytest）。

  python tests/test_epub.py

覆盖三块（对应从 joeseesun/kindle-push-macos 借鉴的两点 + 多列表格新功能）：

  [1] 中文标点规整（typogrify）：中文句末半角 . → 。、中文与全角标点不留空格、
      英文小数点 / 缩写不被误伤。
  [2] epub.py 全形状回归：用一份覆盖所有 Block 种类的 IR 构建 EPUB，再跑上架体检，
      要求零 error；重点守住在 1041 页股东信里新加的多列表格（2/3/5 列）渲染。
  [3] 外部 EPUB 鲁棒读取（epubread）：参照 kindle-push 的 SHAPES，用合成文本造出
      真实世界那些互相不兼容的畸形 EPUB（OPF 在 zip 顶层、单引号 container、
      四种封面声明写法、中文文件名），断言 opf_path 与 extract_cover 四层回退全过。

所有测试书都用合成文本生成，仓库里不塞任何版权内容。
"""
from __future__ import annotations

import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sees_press.epub import build_epub                              # noqa: E402
from sees_press.compat import audit_epub                            # noqa: E402
from sees_press.epubread import extract_cover, opf_path             # noqa: E402
from sees_press.typogrify import typogrify                          # noqa: E402
from sees_press.model import Book, Chapter, Block, Note, Meta       # noqa: E402
from PIL import Image                                               # noqa: E402

FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILED
    print(("  OK   " if cond else "  FAIL ") + name + (("　" + detail) if detail else ""))
    if not cond:
        FAILED += 1


FN = "\ue000fn:1\ue001"   # 脚注引用哨兵（与 inline.py 约定一致）


# ──────────────────────────────────────────────────────────────
# [1] 中文标点规整
# ──────────────────────────────────────────────────────────────
def test_cjk_punctuation() -> None:
    print("\n[1] 中文标点规整（polish.py 借鉴）")
    # 中文句末半角 . → 全角 。
    out = typogrify("他说. 我们看见. 收工.", quote_style="auto")
    check("中文句末 . → 。", out == "他说。 我们看见。 收工。", out)
    # 英文小数点 / 缩写不被误伤
    out = typogrify("增长3.5亿元，U.S. 总统出席。", quote_style="auto")
    check("3.5 与 U.S. 不被误伤", "3.5" in out and "U.S." in out, out)
    # 网址里的点不动（半角 . 前不是汉字）
    out = typogrify("详见 http://example.com 的说明. ", quote_style="auto")
    check("网址与中文句末分别处理", "http://example.com" in out and out.rstrip().endswith("说明。"), out)
    # 中文与全角标点之间不留空格
    out = typogrify("中文 。 他说 。 ", quote_style="auto")
    check("中文前多余空格被吃掉", out == "中文。 他说。 ", out)
    # 半角括号含汉字 → 全角
    out = typogrify("(他说好) 然后走。", quote_style="auto")
    check("半角括号含汉字 → 全角", out == "（他说好） 然后走。", out)
    # 英文为主时不触发（无半角句点改造，但本句含汉字故只验证不崩）
    out = typogrify("Hello world. 你好。", quote_style="auto")
    check("中英混排不崩且中文已规整", "你好。" in out, out)


# ──────────────────────────────────────────────────────────────
# [2] epub.py 全形状回归（重点：多列表格）
# ──────────────────────────────────────────────────────────────
def make_corpus_book(tmp: Path) -> Book:
    img = tmp / "fig.png"
    Image.new("RGB", (120, 80), "#eeeeee").save(img)

    notes = [Note(key="1", text="这是一条脚注，验证 epub:type=footnote 与返回链接。", seq="1")]

    # 真实流水线在 parse.py 里对正文做 typogrify；这里直接构造 Block 绕过了解析，
    # 故手动对段落文本跑一遍 typogrify，镜像真实产出（脚注哨兵 \ue000 等 private-use
    # 字符不会被 typogrify 清掉，可安全透传）。
    tb = lambda t: typogrify(t, quote_style="auto")

    ch1 = Chapter(
        num="第一章", title="多列表格与脚注", level=2, slug="sec01.xhtml",
        blocks=[
            Block(kind="p", text=tb("这是一段正文，测试中文标点规整：他说. 我们看见. 3.5亿元。")),
            Block(kind="p", text=tb(FN + "正文带脚注引用，应生成可弹出脚注与返回链接。")),
            Block(kind="subhead", text="二级标题", level=3),
            Block(kind="table", text="资产\t金额\n现金\t1,234\n设备\t5,678"),                 # 两列
            Block(kind="table", text="年份\t营收\t利润\n1965\t1.2\t0.3\n1970\t2.5\t0.8"),       # 三列带表头
            Block(kind="table", text="项目\t一季度\t二季度\t三季度\t四季度\n"                      # 五列
                                  "A\t10\t20\t30\t40\nB\t5\t15\t25\t35"),
            Block(kind="quote", text="引用一段。\n第二行。"),
            Block(kind="list", items=["列表一", "列表二", "列表三"]),
            Block(kind="olist", items=["第一", "第二"]),
            Block(kind="verse", text="诗句第一行\n诗句第二行"),
            Block(kind="figure", src=str(img), caption="测试插图"),
            Block(kind="rule"),
            Block(kind="code", text="print('hello')"),
            Block(kind="pagebreak"),
        ],
    )
    ch2 = Chapter(
        num="第二章", title="纯文本与重复标点", level=2, slug="sec02.xhtml",
        blocks=[
            Block(kind="p", text=tb("另一章正文！！？？重复标点应被收敛。中文. 英文 OK. 不误伤. ")),
        ],
    )
    meta = Meta(title="回归测试样书", author="见室", language="zh-CN").finalise()
    return Book(meta=meta, chapters=[ch1, ch2], notes=notes)


def test_build_and_audit(tmp: Path) -> None:
    print("\n[2] epub.py 全形状回归 + 上架体检")
    book = make_corpus_book(tmp)
    out = tmp / "corpus.epub"
    build_epub(book, out, cjk_spacing=True)

    check("EPUB 已生成", out.exists())
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        first = z.infolist()[0]
        check("mimetype 包首且 STORED",
              first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
              and z.read("mimetype") == b"application/epub+zip")
        check("nav / opf / ncx 齐备",
              all(any(n.endswith(x) for n in names)
                  for x in ("nav.xhtml", "content.opf", "toc.ncx")))
        sec = z.read("OEBPS/sec01.xhtml").decode("utf-8")
        # 多列表格：3 列与 5 列应有 <thead>，2 列不应有
        check("三列 / 五列表格生成 <thead>", sec.count("<thead>") == 2, f"{sec.count('<thead>')} 个 thead")
        check("两列表格无 thead（兼容写法）", "<thead>" not in
              sec.split("资产")[1].split("</table>")[0])
        check("表格数量正确（3 张）", sec.count("<table") == 3, f"{sec.count('<table')} 张")
        check("脚注带 epub:type=footnote", 'epub:type="footnote"' in sec)
        check("脚注带返回链接", 'class="backref"' in sec)
        check("篇 / 章独立 XHTML", any(re.search(r"/(?:sec|part)\d+\.xhtml$", n) for n in names))
        check("正文标点已规整（。而非半角 .）",
              "他说。" in sec and "3.5亿元。" in sec)

    au = audit_epub(out)
    check("上架体检零 error（重点是多列表格不破坏结构）",
          not au.errors, "; ".join(f"{f.title}:{f.detail}" for f in au.errors))
    if au.warns:
        print("    （提示 " + str(len(au.warns)) + " 项 warn，非阻断）")


# ──────────────────────────────────────────────────────────────
# [3] 外部 EPUB 鲁棒读取（SHAPES）
# ──────────────────────────────────────────────────────────────
def _cover_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1600, 2400), "#cccccc").save(buf, "JPEG")
    return buf.getvalue()


def _assemble_epub(path: Path, *, opf: str, container: str, cover_name: str,
                  opf_dir: str = "OEBPS", extra: dict | None = None) -> None:
    prefix = (opf_dir + "/") if opf_dir else ""      # opf_dir="" 时不能留前导斜杠
    with zipfile.ZipFile(path, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, b"application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr(prefix + "content.opf", opf)
        z.writestr(prefix + cover_name, _cover_bytes())
        for n, d in (extra or {}).items():
            z.writestr(n, d)


def _spine_chapter(opf_dir: str, cover_href: str, cover_item_id: str,
                  meta_cover: str = "", guide: str = "") -> str:
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">urn:uuid:test</dc:identifier>
<dc:title>测试书</dc:title><dc:creator>佚名</dc:creator>
<dc:language>zh</dc:language>{meta_cover}
</metadata>
<manifest>
<item id="{cover_item_id}" href="{cover_href}" media-type="image/jpeg"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine><itemref idref="c1"/></spine>{guide}
</package>"""


def test_external_reader(tmp: Path) -> None:
    print("\n[3] 外部 EPUB 鲁棒读取（SHAPES）")
    cover = _cover_bytes()

    # 形状 A：标准——properties="cover-image" + 双引号 container
    p = tmp / "a_std.epub"
    _assemble_epub(p, opf=_spine_chapter("OEBPS", "images/cover.jpg", "cv",
                                        ).replace('<item id="cv"',
                                                  '<item id="cv" properties="cover-image"'),
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                            'media-type="application/oebps-package+xml"/></rootfiles></container>',
                  cover_name="images/cover.jpg")
    with zipfile.ZipFile(p) as z:
        check("A 标准：opf_path 读出 OEBPS/content.opf", opf_path(z) == "OEBPS/content.opf")
        check("A 标准：extract_cover 命中 cover-image",
              extract_cover(z) == "OEBPS/images/cover.jpg", str(extract_cover(z)))

    # 形状 B：单引号 container.xml（ElementTree 会崩，必须用正则）
    p = tmp / "b_singleq.epub"
    _assemble_epub(p, opf=_spine_chapter("OEBPS", "images/cover.jpg", "cv"
                                        ).replace('<item id="cv"',
                                                  '<item id="cv" properties="cover-image"'),
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            "<rootfiles><rootfile full-path='OEBPS/content.opf' "
                            "media-type='application/oebps-package+xml'/></rootfiles></container>",
                  cover_name="images/cover.jpg")
    with zipfile.ZipFile(p) as z:
        check("B 单引号 container：opf_path 仍可读",
              opf_path(z) == "OEBPS/content.opf")
        check("B 单引号 container：封面仍可定位",
              extract_cover(z) == "OEBPS/images/cover.jpg")

    # 形状 C：OPF 在 zip 顶层（Snow Crash 那种）
    p = tmp / "c_toplevel.epub"
    _assemble_epub(p, opf=_spine_chapter("", "images/cover.jpg", "cv"
                                        ).replace('<item id="cv"',
                                                  '<item id="cv" properties="cover-image"'),
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            '<rootfiles><rootfile full-path="content.opf" '
                            'media-type="application/oebps-package+xml"/></rootfiles></container>',
                  cover_name="images/cover.jpg", opf_dir="")
    with zipfile.ZipFile(p) as z:
        check("C 顶层 OPF：opf_path 读出 content.opf", opf_path(z) == "content.opf")
        got = extract_cover(z)
        check("C 顶层 OPF：封面定位", got == "images/cover.jpg", f"得到 {got!r}")

    # 形状 D：meta name=cover content=item-id
    p = tmp / "d_metaid.epub"
    opf = _spine_chapter("OEBPS", "images/cover.jpg", "coverimg",
                         meta_cover='<meta name="cover" content="coverimg"/>')
    _assemble_epub(p, opf=opf,
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                            'media-type="application/oebps-package+xml"/></rootfiles></container>',
                  cover_name="images/cover.jpg")
    with zipfile.ZipFile(p) as z:
        check("D meta-id：第 2 层回退命中", extract_cover(z) == "OEBPS/images/cover.jpg")

    # 形状 E：meta name=cover content=文件名（手制书常见）
    p = tmp / "e_metafile.epub"
    opf = _spine_chapter("OEBPS", "images/cover.jpg", "coverimg",
                         meta_cover='<meta name="cover" content="cover.jpg"/>')
    _assemble_epub(p, opf=opf,
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                            'media-type="application/oebps-package+xml"/></rootfiles></container>',
                  cover_name="images/cover.jpg")
    with zipfile.ZipFile(p) as z:
        check("E meta-文件名：第 2 层回退按 basename 命中",
              extract_cover(z) == "OEBPS/images/cover.jpg")

    # 形状 F：guide reference 指向封面页，封面页里首图
    p = tmp / "f_guide.epub"
    opf = _spine_chapter("OEBPS", "images/cover.jpg", "coverimg",
                         guide='<guide><reference type="cover" title="Cover" '
                               'href="cover.xhtml"/></guide>')
    _assemble_epub(p, opf=opf,
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                            'media-type="application/oebps-package+xml"/></rootfiles></container>',
                  cover_name="images/cover.jpg",
                  extra={"OEBPS/cover.xhtml":
                          '<html><body><img src="images/cover.jpg"/></body></html>'})
    with zipfile.ZipFile(p) as z:
        check("F guide：第 3 层回退从封面页首图命中",
              extract_cover(z) == "OEBPS/images/cover.jpg")

    # 形状 G：中文文件名封面（路径含非 ASCII）
    p = tmp / "g_cjkname.epub"
    _assemble_epub(p, opf=_spine_chapter("OEBPS", "images/封面.jpg", "cv"
                                        ).replace('<item id="cv"',
                                                  '<item id="cv" properties="cover-image"'),
                  container='<?xml version="1.0"?><container version="1.0" '
                            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                            'media-type="application/oebps-package+xml"/></rootfiles></container>',
                  cover_name="images/封面.jpg")
    with zipfile.ZipFile(p) as z:
        check("G 中文封面文件名：仍可定位",
              extract_cover(z) == "OEBPS/images/封面.jpg")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sees-press-epub-") as t:
        tmp = Path(t)
        test_cjk_punctuation()
        test_build_and_audit(tmp)
        test_external_reader(tmp)
    print("\n" + ("全部通过" if not FAILED else f"{FAILED} 项未通过"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
