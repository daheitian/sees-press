# -*- coding: utf-8 -*-
r"""重做演示站的样张拼图 docs/assets/pages.jpg。

原先那张是 3×2 的六格位图却只放了 5 页，右下角留下一整块与页面同色的空位，
在卡片上看着像图片加载失败。这里固定成 3×2 满六格（上排：封面 / 书名页 / 目录，
下排：篇章首页 / 正文页 / 第二章），底色取站点卡片的 --paper-2，
页与页留白均匀，页边加一道发丝线。

    python scripts/make_docs_assets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "docs" / "assets" / "sample.pdf"
OUT = ROOT / "docs" / "assets" / "pages.jpg"

PAPER = (253, 251, 246)      # --paper-2
HAIR = (232, 227, 216)       # --line-soft
INK_SOFT = (91, 95, 99)      # --ink-soft

CELL_W = 384                 # 单页成品宽度（px）
GAP = 44
MARGIN = 24
LABEL_H = 30                 # 页脚文字占高
SCALE = 2                    # 先放大渲染再降采样，边缘更干净

PAGES = [(1, "封面"), (2, "书名页"), (4, "目录"),
         (5, "篇章首页"), (6, "正文页"), (7, "第二章")]
LAYOUT = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]   # 3 × 2 满格


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def render_page(doc: pymupdf.Document, pno: int) -> Image.Image:
    page = doc[pno - 1]
    zoom = CELL_W * SCALE / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    h = round(CELL_W * page.rect.height / page.rect.width)
    return im.resize((CELL_W, h), Image.LANCZOS)


def main() -> int:
    if not PDF.exists():
        print(f"缺少样例 PDF：{PDF}")
        return 1
    doc = pymupdf.open(PDF)
    cells = [(render_page(doc, pno), pno, label) for pno, label in PAGES]
    cw, ch = cells[0][0].size
    row_h = ch + LABEL_H

    width = 3 * cw + 2 * GAP + 2 * MARGIN
    height = 2 * row_h + GAP + 2 * MARGIN
    canvas = Image.new("RGB", (width, height), PAPER)
    d = ImageDraw.Draw(canvas)
    font = _font(15)

    for (col, row), (im, pno, label) in zip(LAYOUT, cells):
        x = round(MARGIN + col * (cw + GAP))
        y = round(MARGIN + row * (row_h + GAP))
        canvas.paste(im, (x, y))
        d.rectangle([x, y, x + cw - 1, y + ch - 1], outline=HAIR)
        text = f"PAGE {pno} · {label}"
        tw = d.textlength(text, font=font)
        d.text((x + (cw - tw) / 2, y + ch + 7), text, font=font, fill=INK_SOFT)

    canvas.save(OUT, "JPEG", quality=90, optimize=True)
    print(f"{OUT}  {canvas.size[0]}×{canvas.size[1]}  {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
