# -*- coding: utf-8 -*-
r"""程序化编辑印刷风封面（SEES MOOK COVER SYSTEM 的可执行版）。

不依赖 AI 生图，纯 Pillow 生成，保证「输入内容 → 一键成书」永不断链：
  3:4 / 2:3 比例、三段式构图（上部安静纸面 35% / 中部半调墨稿 / 下部版权细线）、
  黑 + 暖白双色、铜版网点质感、极细线框与轻微套印错位。

标题在 PNG 里烤死 —— 微信读书书架取的是 cover-image 指向的图片本身，
不是 cover.xhtml 的渲染结果，光在页面叠字是看不见的。
若已有 mono-color 出的底图或 bake_cover.py 烤好的成品，用 --cover 直接接管。
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .styles import get
from .model import Meta

W, H = 1600, 2400                       # 2:3，EPUB 常规比例
FONT_HEI = "C:/Windows/Fonts/simhei.ttf"
FONT_KAI = "C:/Windows/Fonts/simkai.ttf"
FONT_SONG = "C:/Windows/Fonts/simsun.ttc"
FONT_FALLBACK = "C:/Windows/Fonts/msyh.ttc"


def _font(size: int, kind: str = "hei") -> ImageFont.FreeTypeFont:
    cands = {"hei": [FONT_HEI, FONT_FALLBACK], "kai": [FONT_KAI, FONT_HEI],
             "song": [FONT_SONG, FONT_HEI]}.get(kind, [FONT_HEI])
    for p in cands:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex(rgb: str) -> tuple[int, int, int]:
    s = rgb.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _paper_noise(im: Image.Image, seed: int, amount: int = 5) -> None:
    """极轻的纸纹，不能抢走留白。"""
    rnd = random.Random(seed)
    px = im.load()
    w, h = im.size
    for _ in range(w * h // 26):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r, g, b = px[x, y][:3]
        d = rnd.randint(-amount, amount)
        px[x, y] = (max(0, min(255, r + d)), max(0, min(255, g + d)),
                    max(0, min(255, b + d)))


def make_base(meta: Meta, *, ratio: str = "2:3") -> Image.Image:
    """三段式底图。返回 RGB 图，标题尚未烤入。"""
    s = get(meta.style)
    paper = _hex(s["cover_paper"])
    ink = _hex(s["cover_ink"])
    w = W
    h = int(W * 4 / 3) if ratio == "3:4" else H
    im = Image.new("RGB", (w, h), paper)
    d = ImageDraw.Draw(im)

    seed = sum(ord(c) for c in meta.title) if meta.title else 7
    _paper_noise(im, seed)

    # --- 上段：35% 安静纸面（标题区），只放一根专色细线与小字眉题 ---
    top_end = int(h * 0.355)
    margin = int(w * 0.075)
    d.line([(margin, int(h * 0.085)), (margin + int(w * 0.16), int(h * 0.085))],
           fill=ink, width=4)
    if meta.series:
        d.text((margin, int(h * 0.045)), meta.series, font=_font(30, "kai"), fill=ink)

    # --- 中段：半调铜版墨稿（偏心球体 + 左侧实墨块 + 斜切线） ---
    cx, cy = int(w * 0.635), int(h * 0.605)
    radius = int(w * 0.295)
    step = 9
    rnd = random.Random(seed + 1)
    for y in range(cy - radius, cy + radius, step):
        for x in range(cx - radius, cx + radius, step):
            dist = math.hypot(x - cx, y - cy)
            if dist > radius:
                continue
            # 光照：左上亮、右下暗 → 网点半径随距离与角度变化
            lit = 0.5 + 0.5 * ((x - cx) + (y - cy)) / (radius * 1.9)
            density = max(0.0, min(1.0, 0.18 + 0.82 * lit))
            r = (step / 2) * density * 1.28
            if r < 0.55:
                continue
            jitter = rnd.uniform(-0.6, 0.6)
            d.ellipse([x + jitter - r, y + jitter - r, x + jitter + r, y + jitter + r],
                      fill=ink)
    # 轻微套印错位：主视觉轮廓以极低透明度偏移 5px 再叠一次（印刷感）
    off = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(off)
    od.ellipse([cx - radius + 5, cy - radius + 3, cx + radius + 5, cy + radius + 3],
               outline=(*ink, 26), width=2)
    im = Image.alpha_composite(im.convert("RGBA"), off).convert("RGB")
    d = ImageDraw.Draw(im)

    # 左侧实墨竖块（三段式的「墨」）
    bw, bh = int(w * 0.048), int(h * 0.19)
    bx, by = margin, int(h * 0.52)
    d.rectangle([bx, by, bx + bw, by + bh], fill=ink)
    # 斜切线
    d.line([(margin, int(h * 0.775)), (w - margin, int(h * 0.735))], fill=ink, width=2)

    # --- 下段：细线 + 出品方/年份 ---
    d.line([(margin, int(h * 0.885)), (w - margin, int(h * 0.885))], fill=ink, width=2)
    pub = meta.publisher or "见室小册"
    d.text((margin, int(h * 0.905)), pub, font=_font(30, "song"), fill=ink)
    yr = (meta.date or "")[:4]
    if yr:
        tw = d.textlength(yr, font=_font(28, "song"))
        d.text((w - margin - tw, int(h * 0.905)), yr, font=_font(28, "song"), fill=ink)

    # 极细线框
    d.rectangle([int(w * 0.035), int(h * 0.026), int(w * 0.965), int(h * 0.974)],
                outline=(*ink,), width=1)
    return im


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          max_width: int, max_lines: int = 3) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=font) > max_width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max(1, len(lines[-1]) - 1)] + "…"
    return lines


def bake_title(im: Image.Image, meta: Meta) -> Image.Image:
    """把中文书名/副题/作者烤进上部留白区（长标题自动缩放 + 折行）。"""
    s = get(meta.style)
    ink = _hex(s["cover_ink"])
    d = ImageDraw.Draw(im)
    w, h = im.size
    margin = int(w * 0.075)
    max_w = w - margin * 2

    title = meta.title or "未命名"
    n = len(title)
    size = 126 if n <= 8 else (104 if n <= 14 else (86 if n <= 22 else 70))
    f = _font(size, "hei")
    lines = _wrap(d, title, f, max_w, 3)
    while len(lines) > 2 and size > 52:
        size -= 6
        f = _font(size, "hei")
        lines = _wrap(d, title, f, max_w, 3)

    ceiling = int(h * 0.345)            # 标题区下沿，绝不侵入中段主视觉
    y = int(h * 0.135)
    leading = int(size * 1.34)
    for ln in lines:
        if y + leading > ceiling:
            break
        d.text((margin, y), ln, font=f, fill=ink)
        y += leading
    y += int(size * 0.22)

    if meta.subtitle:
        fs = _font(38, "kai")
        for ln in _wrap(d, meta.subtitle, fs, max_w, 1):
            if y + 54 > ceiling:
                break
            d.text((margin, y), ln, font=fs, fill=ink)
            y += 54
    author = meta.author_line() or meta.author
    if author:
        fa = _font(40, "kai")
        ay = min(int(h * 0.305), ceiling - 52)
        if ay >= y - 10:
            d.text((margin, ay), author, font=fa, fill=ink)
    return im


def make_cover(meta: Meta, out: str | Path | None = None, *,
               fmt: str = "png", ratio: str = "2:3") -> bytes | Path:
    """生成含标题的封面。给 out 则写文件并返回 Path，否则返回图片字节。"""
    im = bake_title(make_base(meta, ratio=ratio), meta)
    if out is not None:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, fmt.upper())
        return p
    buf = io.BytesIO()
    im.save(buf, fmt.upper())
    return buf.getvalue()
