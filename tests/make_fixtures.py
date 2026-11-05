# -*- coding: utf-8 -*-
r"""生成 PDF 抽取回归夹具（不依赖 TeX，只需 PyMuPDF + Pillow）。

  python tests/make_fixtures.py

产出：
  tests/fixtures/双栏样书.pdf   双栏 / 页眉页码 / 版权页 / 目录页 / 插图 / 脚注 / 无书签
  tests/fixtures/扫描件.pdf     纯图片、无文本层（应触发明确报错）
  tests/fixtures/fig.png        插图源

用手写逐行坐标而不是自动排版，是为了精确复刻真实 PDF 里那些会让
抽取器翻车的几何特征：首行缩进、悬挂缩进、两栏起始 y 相同、页脚页码。
"""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "fixtures"
FIX.mkdir(exist_ok=True)
FONT = Path("C:/Windows/Fonts/simhei.ttf")
W, H = 460.0, 620.0
BODY, LEAD = 10.5, 17.0
LEFT_X, RIGHT_X = 40.0, 245.0
PER_LINE = 14

PARAGRAPHS = [
    "我们今天讨论纸张，其实是在讨论一种被忽略的时间。纸张不会主动说话，它只是把写字的人留下的犹豫与停顿一并收进纤维里。",
    "一本书的顺序并不等于写它的顺序。读者从第一页走到最后一页，作者却常常从中间某句开始，再向两端慢慢补完整。",
    "排版是一种沉默的判断：哪里该留白，哪里该断开，哪里让一句话独自占住一行，都在替读者预先决定呼吸的节奏。",
    "屏幕把文字变成可以无限滚动的水，纸却给每段话一个固定位置。位置本身也是意义，翻回第三页时手指记得住。",
    "译文里的一个逗号，有时比整段话更难安放。它既属于前一句的余音，又替后一句预留了起势，删掉就读快了半拍。",
    "所谓重排，不是把同一行字换个地方放，而是先承认原书的页面只是临时的容器，再把内容从容器里完整取出来。",
    "校对的人最怕的不是错字，是顺序没错却气息不对。句子都还在，可读起来像有人替你换过一次呼吸。",
    "因此一份好的电子书，目录应当自动生成，脚注应当能点开，章节应当能跳转；这些都来自结构，而不是来自版式。1",
    "把书稿当作唯一真相源，是一次很朴素的决定：所有成品都从它派生，谁也不从谁身上反推，改一处便处处一致。",
    "最后剩下的问题仍然很旧：机器负责把字摆好，人负责判断这句话到底停在哪里。分工如此，倒也清楚。",
    "页脚的小数字是给纸本用的，到了屏幕上就成了噪音；识别并拿掉它，比事后手工删一百遍都要可靠。",
    "而双栏最容易被读错：左栏读完才轮到右栏，若按纵坐标一行行扫，两段话会像洗牌一样交缠在一起。",
]


def _page(doc):
    pg = doc.new_page(width=W, height=H)
    pg.insert_font(fontname="F0", fontfile=str(FONT))
    return pg


def _t(pg, x, y, s, size=BODY):
    pg.insert_text((x, y), s, fontname="F0", fontfile=str(FONT),
                   fontsize=size, color=(0, 0, 0))


def _wrap(s: str, per_line: int = PER_LINE):
    return [s[i:i + per_line] for i in range(0, len(s), per_line)]


def _para(pg, s, x, y, first_indent=True):
    for i, ln in enumerate(_wrap(s)):
        _t(pg, x + (BODY * 2 if i == 0 and first_indent else 0), y, ln)
        y += LEAD
    return y + LEAD * 0.45


def _chrome(pg, no, header="见室译丛 · 双栏测试"):
    _t(pg, LEFT_X, 30, header, 9)
    _t(pg, W / 2 - 4, H - 28, str(no), 9)


def make_figure():
    import random
    rng = random.Random(7)
    im = Image.new("RGB", (300, 180), "#F2EFE7")
    d = ImageDraw.Draw(im)
    d.rectangle([18, 18, 281, 161], outline="#3B4A42", width=3)
    for i in range(9):
        d.line([(40, 48 + i * 12), (260 - i * 8, 48 + i * 12)], fill="#7C8B82", width=2)
    # 细颗粒让 PNG 有真实插图的体积（校验器会把 <1KB 的图片判为异常小）
    for _ in range(3200):
        x, y = rng.randrange(300), rng.randrange(180)
        v = rng.randrange(150, 225)
        d.point((x, y), fill=(v, v, v - 6))
    p = FIX / "fig.png"
    im.save(p)
    return p


def make_book():
    fig = make_figure()
    doc = pymupdf.open()

    # 1. 书名页
    pg = _page(doc)
    _t(pg, 130, 250, "双栏测试文集", 24)
    _t(pg, 175, 290, "见室 编", 12)
    _t(pg, 165, 470, "见室小册", 10)

    # 2. 版权页
    pg = _page(doc)
    _t(pg, 190, 150, "版权信息", 15)
    for i, ln in enumerate(["书名　双栏测试文集", "出版　见室小册",
                            "版次　2026 年 11 月第 1 版",
                            "ISBN　978-0-000-00000-0", "版权所有　侵权必究"]):
        _t(pg, 90, 220 + i * 26, ln, 9.5)

    # 3. 目录页（右对齐页码）
    pg = _page(doc)
    _t(pg, 205, 100, "目 录", 15)
    for i, (t, n) in enumerate([("第一章　山与海", "4"),
                                ("第二章　纸上的时间", "5"),
                                ("第三章　重排之后", "6")]):
        _t(pg, 60, 160 + i * 40, t)
        _t(pg, 400, 160 + i * 40, n)

    # 4~6. 双栏正文：每页左右各两段，刻意让两栏起始 y 相同
    heads = [(3, "第一章", "山与海"), (4, "第二章", "纸上的时间"),
             (5, "第三章", "重排之后")]
    for k, (idx, chnum, chname) in enumerate(heads):
        pg = _page(doc)
        _chrome(pg, idx + 1)
        y = 92 if k == 0 else 70
        _t(pg, LEFT_X, y, chnum, 14)
        y += 26
        _t(pg, LEFT_X + 21, y, chname, 18)
        y += 34
        y = _para(pg, PARAGRAPHS[k * 4], LEFT_X, y)
        y = _para(pg, PARAGRAPHS[k * 4 + 1], LEFT_X, y)
        if k == 0:
            pg.insert_image(pymupdf.Rect(LEFT_X, y + 8, LEFT_X + 150, y + 98),
                            filename=str(fig))
        ry = 70 + (34 if k == 0 else 0)
        ry = _para(pg, PARAGRAPHS[k * 4 + 2], RIGHT_X, ry)
        _para(pg, PARAGRAPHS[k * 4 + 3], RIGHT_X, ry)

    # 最后一页加一条页底脚注
    pg = doc[-1]
    _t(pg, LEFT_X, H - 74, "1 这是页底脚注，用于验证编号行抽取。", 7.5)

    out = FIX / "双栏样书.pdf"
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    print("wrote", out)


def make_scanned():
    img = Image.new("RGB", (900, 1200), "#FBF9F3")
    d = ImageDraw.Draw(img)
    for i in range(20):
        d.line([(100, 140 + i * 46), (800 - (i % 4) * 60, 140 + i * 46)],
               fill="#D8D4C9", width=12)
    p = FIX / "_scan.png"
    img.save(p)
    doc = pymupdf.open()
    pg = doc.new_page(width=460, height=620)
    pg.insert_image(pg.rect, filename=str(p))
    out = FIX / "扫描件.pdf"
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    print("wrote", out)


if __name__ == "__main__":
    make_book()
    make_scanned()
