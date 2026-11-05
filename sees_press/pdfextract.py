# -*- coding: utf-8 -*-
r"""PDF → 结构化书稿（可重排的语义 Markdown）。

设计前提：PDF 是分页的**视觉**产物，EPUB 是**重排的语义**产物。把 PDF 的
文本层按行倒出来直接打包，只会得到一本每页都断行、夹着页眉页码、没有目录的
劣质电子书。这里做的是「重建结构」，对应 Standard Ebooks 里
`create-draft / split-file / semanticate / build-toc` 的逆向工程：

  逐页 span → 行 → 栏 → 段落 → 标题/列表/引文/脚注/图片 → Markdown → IR

关键处理（都是实测容易翻车的地方）：
  1. 中文 PDF 常把每个字拆成独立 span：按行合并，按 x 间隙补空格。
  2. 页眉 / 页脚 / 页码：跨页重复 + 上下边缘带双重判定，避免误删正文首行。
  3. 双栏：按 x 中心最大间隙切栏，先左后右排序，否则正文会左右串行。
  4. 断行合并：首行缩进、段间空距、上一行是否为不满行的段末行三信号，
     并允许段落跨页续接（否则每页开头都会碎出一段）。
  5. 章节：优先用 PDF 大纲（bookmarks），无大纲时回退字号 / 加粗 / 章名正则。
  6. 脚注与插图：底部小字号编号行抽成 [^n]，图片按 bbox 插入原位置。
  7. 扫描件（无文本层）直接明确报错，绝不静默产出空书。

产物除 Markdown 外还有一份 diagnostics，用于把「重建质量」写进质检报告：
字符保留率、剔除的页眉页脚、双栏页数、大纲命中率、识别到的章节/图片/脚注。

只依赖 PyMuPDF（可选依赖；缺失时给出清晰提示），其余全标准库。
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import pymupdf
except ImportError:                                    # 老版本叫 fitz
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None


# ── 文本 ────────────────────────────────────────────────────────────
LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st", "\u0132": "IJ",
    "\u0133": "ij", "\u0152": "OE", "\u0153": "oe", "\u2010": "-",
}
CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_RE = re.compile(rf"[{CJK}]")
CJK_GAP = re.compile(rf"(?<=[{CJK}])[ \t]{{1,3}}(?=[{CJK}])")
END_PUNCT = tuple("。！？；：…」』】）》”’\"'!?;:")
SENT_END = tuple("。！？…!?")
SOFT_CHARS = "\u00ad\u200b\u200c\u200d\u2060\ufeff"

CHAPTER_RE = re.compile(
    r"^(?:第\s*[零〇一二三四五六七八九十百千两0-9０-９]+\s*[章节篇部卷回讲]"
    r"|卷\s*[零〇一二三四五六七八九十百千两0-9０-９]+"
    r"|[0-9０-９]{1,3}\s*[、.．]"
    r"|(?:chapter|part|book|section)\s+[0-9IVXLC]+"
    r"|[零〇一二三四五六七八九十百千两]{1,3}\s*[、.．]"
    r"|(?:序章|序言|自序|代序|序|前言|导言|导论|绪论|引子|楔子"
    r"|后记|跋|尾声|附录|参考文献|注释|致谢|代跋)(?=\s|[:：　]|$))",
    re.I,
)
FRONT_BACK = re.compile(
    r"^(序章|序言|自序|代序|序|前言|导言|导论|绪论|引子|楔子|"
    r"后记|跋|尾声|附录|参考文献|注释|致谢|代跋)(\s|[:：　]|$)")
BULLET_RE = re.compile(r"^\s*(?:[\u2022\u00b7\u25aa\u25e6\u2023\u2043\u2219\*\-\u2013]\s+|"
                       r"[\u2460-\u2473\u24ea]\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*)")
OLIST_RE = re.compile(r"^\s*(?:\d{1,3}[.)、．]|[（(]\d{1,3}[）)])\s*")
PAGENUM_RE = re.compile(r"^\W{0,4}(?:[-–—]\s*)?(?:第\s*)?\d{1,4}\s*(?:页)?\s*(?:[/／]\s*\d{1,4})?\s*(?:[-–—]\s*)?\W{0,4}$")
FOOT_MARK = re.compile(r"^\s*(?:\[(\d{1,3})\]|[\(（](\d{1,3})[）)]|"
                       r"([①②③④⑤⑥⑦⑧⑨⑩⓪*†‡])\s*|(\d{1,3})\s*(?=[^0-9\s]))")
COPYRIGHT_RE = re.compile(r"版权|图书在版编目|CIP|ISBN|出版|发行|印张|字数|版次|"
                          r"定价|责任编辑|印刷|开本|书号|著作权", re.I)
TOC_TITLE_RE = re.compile(r"^\s*(目\s*录|目\s*次|contents?|table of contents)\s*$", re.I)
FRONT_OUTLINE_RE = re.compile(r"^(封面|封底|书名|扉页|版权|目录|目次|cover|title|"
                              r"copyright|contents?|toc|half.?title)", re.I)
CHAPTER_NUM_RE = re.compile(r"^\s*(?:第\s*[零〇一二三四五六七八九十百千两0-9０-９]+\s*"
                            r"[章节篇部卷回讲]|卷\s*[零〇一二三四五六七八九十百千两0-9０-９]+|"
                            r"[0-9０-９]{1,3}\s*[、.．]?)\s*$")
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".jpx", ".jp2", ".tiff"}


class PDFError(RuntimeError):
    """可预期、且值得直接展示给用户的 PDF 处理错误。"""


def clean_text(s: str) -> str:
    """连字还原、零宽字符清理、中文之间误插的空格删除。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    for a, b in LIGATURES.items():
        s = s.replace(a, b)
    s = "".join(c for c in s if c not in SOFT_CHARS)
    s = s.replace("\u00a0", " ").replace("\u3000", " ").replace("\t", " ")
    s = CJK_GAP.sub("", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s.strip()


def _norm_key(s: str) -> str:
    """页眉页脚去重用的规范化：抹掉数字与所有非字母汉字。"""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\d+", "#", s)
    return re.sub(rf"[^{CJK}a-z]+", "", s)


def _ends_punct(s: str) -> bool:
    return bool(s) and s[-1] in END_PUNCT


def _sentence_end(s: str) -> bool:
    return bool(s) and s[-1] in SENT_END


def _is_cjk(s: str) -> bool:
    return bool(CJK_RE.search(s or ""))


# ── 数据 ────────────────────────────────────────────────────────────
@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    italic: bool
    page: int
    col: int = 0
    kind: str = ""                 # p / head / sub / list / olist / quote /
                                   # foot / header / image / caption / title
    head_level: int = 0            # 1 = ## 章, 2 = ### 节, 3 = #### 小节
    src: str = ""                  # image 的相对路径
    caption: str = ""
    href: str = ""
    seq: int = 0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Extracted:
    markdown: str
    diagnostics: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)
    assets: list[str] = field(default_factory=list)


@dataclass
class _Page:
    no: int
    w: float
    h: float
    lines: list[Line] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    two_col: bool = False
    split: float = 0.0
    front: bool = False               # 封面 / 书名页 / 版权页 / 目录页
    toc: bool = False


# ── 逐页解析 ────────────────────────────────────────────────────────
def _read_lines(doc, page_no: int) -> tuple[list[Line], list[dict]]:
    page = doc[page_no]
    data = page.get_text("dict")
    lines: list[Line] = []
    images: list[dict] = []
    for block in data.get("blocks", []):
        if block.get("type") == 1:                   # 图片块（自带像素与位置）
            images.append({"bbox": block.get("bbox"), "image": block.get("image"),
                           "width": block.get("width", 0), "height": block.get("height", 0),
                           "ext": block.get("ext", "png")})
            continue
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            spans = ln.get("spans") or []
            if not spans:
                continue
            parts: list[str] = []
            weighted = 0.0
            chars = 0
            bold = italic = False
            sup: list[tuple[str, float]] = []
            for k, sp in enumerate(spans):
                t = sp.get("text") or ""
                if not t:
                    continue
                if parts:                            # x 间隙补空格
                    gap = sp["bbox"][0] - spans[k - 1]["bbox"][2]
                    sz = float(sp.get("size") or 10)
                    if gap > sz * 0.26 and not parts[-1].endswith(" ") \
                            and not t.startswith(" ") and not _is_cjk(t[:1] + parts[-1][-1:]):
                        parts.append(" ")
                parts.append(t)
                n = len(t)
                sz = float(sp.get("size") or 10)
                weighted += sz * n
                chars += n
                flags = int(sp.get("flags") or 0)
                fname = str(sp.get("font") or "")
                b = bool(flags & 16) or bool(re.search(r"Bold|Heavy|Black", fname, re.I))
                i = bool(flags & 2) or bool(re.search(r"Italic|Oblique", fname, re.I))
                bold |= b
                italic |= i
                if sz and n and re.fullmatch(r"\s*(?:\d{1,3}|[①②③④⑤⑥⑦⑧⑨⑩*†‡])\s*", t):
                    sup.append((t.strip(), sz))
            text = clean_text("".join(parts))
            if not text or not chars:
                continue
            x0, y0, x1, y1 = [float(v) for v in ln["bbox"]]
            lines.append(Line(text=text, x0=x0, y0=y0, x1=x1, y1=y1,
                              size=round(weighted / chars, 2), bold=bold,
                              italic=italic, page=page_no))
            lines[-1].href = ""                    # 链接稍后按 bbox 回填
            lines[-1].seq = len(lines) - 1
    # URI 链接：只有覆盖整行时才转 Markdown 链接，避免把半个句子拆坏
    try:
        for lk in page.get_links():
            if lk.get("kind") != 2 and "uri" not in lk:   # 2 = LINK_URI
                continue
            uri = str(lk.get("uri") or "")
            if not uri or ")" in uri or not uri.startswith(("http", "mailto")):
                continue
            r = lk.get("from")
            if not r:
                continue
            rx0, ry0, rx1, ry1 = [float(v) for v in r]
            for l in lines:
                if abs(l.y0 - ry0) < l.size and abs(l.y1 - ry1) < l.size * 1.5 \
                        and (min(l.x1, rx1) - max(l.x0, rx0)) > l.w * 0.75:
                    l.href = uri
                    break
    except Exception:
        pass
    return lines, images


def _body_size(all_lines: list[Line]) -> float:
    """按字符数加权的字号众数 = 正文字号。"""
    hist: Counter[float] = Counter()
    for l in all_lines:
        hist[round(l.size * 2) / 2] += len(l.text)
    if not hist:
        return 10.0
    return hist.most_common(1)[0][0]



def _detect_columns(page: _Page, body: float) -> None:
    """双栏判据：几乎没有行跨过版心中线，且左右两组都成规模、中间有真空带。

    只按 x 中心找最大间隙会误伤「有首行缩进 + 居中题词」的单栏中文书，
    所以先做跨中线检查。
    """
    ls = [l for l in page.lines if l.kind not in ("header", "front")]
    if len(ls) < 12:
        return
    mid = page.w / 2
    tol = page.w * 0.03
    cross = sum(1 for l in ls if l.x0 < mid - tol and l.x1 > mid + tol)
    if cross > len(ls) * 0.15:
        return
    xs = sorted(l.cx for l in ls)
    lo, hi = int(len(xs) * 0.15), int(len(xs) * 0.85)
    gaps = [(xs[i + 1] - xs[i], (xs[i] + xs[i + 1]) / 2) for i in range(lo, hi)]
    if not gaps:
        return
    gap, split = max(gaps)
    left = sum(1 for x in xs if x < split)
    right = len(xs) - left
    if gap > page.w * 0.055 and left >= 5 and right >= 5:
        page.two_col = True
        page.split = split
        for l in page.lines:
            l.col = 0 if l.cx < split else 1



def _mark_headers(pages: list[_Page]) -> int:
    """跨页重复 + 上下边缘带 + 纯页码，剔除页眉页脚。"""
    band_top, band_bottom = 0.085, 0.86
    seen: Counter[str] = Counter()
    for pg in pages:
        keys = set()
        for l in pg.lines:
            if l.y0 < pg.h * band_top or l.y1 > pg.h * band_bottom:
                k = _norm_key(l.text)
                if len(k) >= 2:
                    keys.add(k)
        for k in keys:
            seen[k] += 1
    threshold = max(2, math.ceil(len(pages) * 0.34))
    repeated = {k for k, n in seen.items() if n >= threshold}
    dropped = 0
    for pg in pages:
        for l in pg.lines:
            top = l.y0 < pg.h * band_top
            bottom = l.y1 > pg.h * band_bottom
            if not (top or bottom):
                continue
            k = _norm_key(l.text)
            is_num = bool(PAGENUM_RE.match(l.text)) and len(l.text) <= 24
            if is_num and bottom:
                l.kind, dropped = "header", dropped + 1
            elif k and k in repeated and len(l.text) <= 60:
                l.kind, dropped = "header", dropped + 1
    return dropped



def _is_toc_page(pg: _Page) -> bool:
    ls = [l for l in pg.lines if l.kind != "header"]
    if not ls:
        return False
    has_title = any(TOC_TITLE_RE.match(l.text) for l in ls)
    right_nums = sum(1 for l in ls
                     if re.fullmatch(r"\d{1,4}", l.text.strip()) and l.x0 > pg.w * 0.6)
    dot_leaders = sum(1 for l in ls if re.search(r"[.．·]{3,}\s*\d{1,4}\s*$", l.text))
    return ((has_title and right_nums >= 1) or right_nums >= 3 or dot_leaders >= 3)


def _is_front_page(pg: _Page, meta: dict) -> bool:
    ls = [l for l in pg.lines if l.kind != "header"]
    if not ls:
        return True                                   # 纯图封面 / 空白页
    if len(ls) > 26:
        return False
    joined = " ".join(l.text for l in ls)
    if len(COPYRIGHT_RE.findall(joined)) >= 2:
        return True
    t = _norm_key(str(meta.get("title") or ""))
    if t and any(_norm_key(l.text) == t for l in ls) and len(ls) <= 10:
        return True
    # 行数很少且有大字：书名页 / 半标题页 / 献词页
    if len(ls) <= 6 and any(l.size >= 12 for l in ls):
        return True
    return False


def _mark_frontmatter(pages: list[_Page], outline: list, meta: dict) -> tuple[int, int]:
    """EPUB 阅读器自带封面 / 书名页 / 版权页 / 导航，PDF 的这些前置页应剔除，
    否则成品里会冒出「第一章：书名 论闲暇」这种荒唐章节。
    """
    for pg in pages:
        if _is_toc_page(pg):
            pg.front = pg.toc = True
    start = None
    for pg in pages:
        if pg.front:
            continue
        if any(CHAPTER_RE.match(l.text.strip()) and len(l.text) <= 42
               for l in pg.lines if l.kind not in ("header", "foot")):
            start = pg.no
            break
    if start is None:
        for lvl, title, pgno in sorted(outline, key=lambda x: x[2]):
            if FRONT_OUTLINE_RE.match(title):
                continue
            if 0 <= pgno < len(pages) and not pages[pgno].front:
                start = pgno
                break
    if start is None:
        return 0, sum(1 for pg in pages if pg.toc)
    for pg in pages[:start]:
        if not pg.front and _is_front_page(pg, meta):
            pg.front = True
    for pg in pages:
        if pg.front:
            for l in pg.lines:
                l.kind = "front"
    return sum(1 for pg in pages if pg.front), sum(1 for pg in pages if pg.toc)


# ── 结构识别 ────────────────────────────────────────────────────────
def _outline_map(doc) -> list[tuple[int, str, int]]:
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        return []
    out = []
    for item in toc:
        try:
            lvl, title, pg = int(item[0]), str(item[1]).strip(), int(item[2])
        except Exception:
            continue
        if title and pg > 0:
            out.append((lvl, clean_text(title), pg - 1))
    return out



def _apply_outline(pages: list[_Page], outline: list[tuple[int, str, int]],
                   body: float) -> int:
    """大纲标题去正文里定位。命中足够多时以大纲为准，比字号猜更可靠。

    实测坑：排版工具常把「第一章」「章名」排成上下两行，而书签里是完整的一行；
    若只模糊匹配单行，会得到「### 第一章 / ## 章名」这种倒置层级。这里把连续
    命中的行簇合并，直接采用书签里的标题文本。
    """
    items = [(lvl, t, pg) for lvl, t, pg in outline if not FRONT_OUTLINE_RE.match(t)]
    if len(items) < 2:
        return 0
    base = min(lvl for lvl, _, _ in items)
    hits = 0
    for lvl, title, pg in items:
        key = _norm_key(title)
        if not key:
            continue
        cands: list[Line] = []
        for pno in range(max(0, pg - 1), min(len(pages), pg + 2)):
            for l in pages[pno].lines:
                if l.kind in ("header", "foot", "image", "front"):
                    continue
                lk = _norm_key(l.text)
                if lk and len(lk) >= 2 and (lk == key or lk in key or key in lk):
                    cands.append(l)
        if not cands:
            continue
        cands.sort(key=lambda l: (l.page, l.y0))
        first = cands[0]
        cluster = [l for l in cands
                   if l.page == first.page and 0 <= l.y0 - first.y0 < body * 3.0]
        if not cluster:
            continue
        first.text = title
        first.kind = "head"
        first.head_level = min(3, max(1, lvl - base + 1))
        for l in cluster[1:]:
            l.kind = "front"                 # 被书签合并掉的第二行
        hits += 1
    return hits


def _classify(pages: list[_Page], body: float, outline_hits: int,
              outline_len: int) -> int:
    """启发式标题识别（大纲不足时兜底）。返回识别到的章节数。"""
    chapter_words = ("序", "前言", "导言", "导论", "绪论", "引子", "楔子",
                     "后记", "跋", "尾声", "附录", "参考文献", "致谢")
    n_chapters = 0
    for pg in pages:
        if pg.front:
            continue
        usable = [l for l in pg.lines if l.kind not in ("header", "front")]
        for idx, l in enumerate(usable):
            if l.kind in ("head", "sub"):
                if l.kind == "head":
                    n_chapters += 1
                continue
            t = l.text.strip()
            if not t:
                continue
            # 1) 章名正则
            m = CHAPTER_RE.match(t)
            is_word = bool(FRONT_BACK.match(t))
            if (m or is_word) and len(t) <= 42 and not _ends_punct(t):
                l.kind, l.head_level = "head", 1
                n_chapters += 1
                continue
            # 2) 显著字号
            if l.size >= body * 1.34 and len(t) <= 46:
                l.kind, l.head_level = "head", 1
                n_chapters += 1
                continue
            if l.size >= body * 1.14 and len(t) <= 40:
                l.kind, l.head_level = "sub", 2
                continue
            # 3) 加粗短行 + 上下有空距（小节）
            prev = usable[idx - 1] if idx else None
            nxt = usable[idx + 1] if idx + 1 < len(usable) else None
            gap_before = (l.y0 - prev.y1) if prev and prev.page == l.page else 99
            gap_after = (nxt.y0 - l.y1) if nxt and nxt.page == l.page else 99
            if l.bold and len(t) <= 30 and not _ends_punct(t) \
                    and (gap_before > body * 0.65 or gap_after > body * 0.65):
                l.kind, l.head_level = "sub", 2
                continue
            # 4) 列表
            if BULLET_RE.match(t) and len(t) > 1:
                l.kind = "list"
                continue
            if OLIST_RE.match(t):
                l.kind = "olist"
                continue
            # 5) 底部小字号脚注定义
            if l.y0 > pg.h * 0.76 and l.size < body * 0.95 and FOOT_MARK.match(t):
                l.kind = "foot"
    _refine_blocks(pages, body)
    # 章号与章名两行已合并成一行，统计以最终结构为准
    return sum(1 for pg in pages for l in pg.lines if l.kind == "head")




def _refine_blocks(pages: list[_Page], body: float) -> None:
    """三处「版面看得出来、纯字号看不出来」的结构：
      章号与章名是两行、章首大字题词、居中短行诗。
    """
    for pg in pages:
        if pg.front:
            continue
        ls = [l for l in pg.lines if l.kind not in ("header", "front", "image")]

        # 3a. 「第一章」+「闲暇不是空闲」→ 一行章标题（无书签时兜底）
        for a, b in zip(ls, ls[1:]):
            if a.kind in ("head", "sub") and b.kind in ("head", "sub") \
                    and a.page == b.page \
                    and (b.y0 - a.y1) < max(a.size, b.size) * 1.5 \
                    and CHAPTER_NUM_RE.match(a.text):
                b.text = a.text.strip() + "　" + b.text.strip()
                b.kind, b.head_level = "head", 1
                a.kind = "front"

        usable = [l for l in ls if l.kind != "front"]
        plain = [l for l in usable if l.kind == ""]
        global_edges = _edges(plain) if plain else (0.0, pg.w)
        # 双栏页的右栏，相对全局版心看起来「整栏都居中」；按栏取版心才不会
        # 把右栏正文错认成题词或诗歌。
        col_edges: dict[int, tuple[float, float]] = {}
        if pg.two_col:
            for col in {l.col for l in plain}:
                g = [l for l in plain if l.col == col]
                if len(g) >= 3:
                    col_edges[col] = _edges(g)

        def geom(x: Line) -> tuple[float, float]:
            return col_edges.get(x.col, global_edges) if pg.two_col else global_edges

        # 3b. 连续的大字缩进行（章首题词 / 引语）。已按字号误判为 sub 的
        #     行也要收回来，否则题词会变成一串「### 小标题」。
        i = 0
        while i < len(usable):
            l = usable[i]
            if l.kind in ("", "sub") and l.size >= body * 1.09 \
                    and l.x0 > geom(l)[0] + body * 1.2:
                j = i + 1
                while j < len(usable) and usable[j].kind in ("", "sub") \
                        and usable[j].size >= body * 1.08 \
                        and usable[j].x0 > geom(usable[j])[0] + body * 0.9 \
                        and 0 <= (usable[j].y0 - usable[j - 1].y1) < body * 1.3:
                    j += 1
                for k in range(i, j):
                    usable[k].kind = "quote"
                i = j
            else:
                i += 1

        # 3c. 连续居中短行：无句末标点 = 诗，带句末标点 = 引文。
        #     每一行都要满足居中几何，否则正文段的续行会被吞进诗里。
        def centered(x: Line) -> bool:
            gl, gr = geom(x)
            return (x.kind == "" and x.x0 > gl + body * 2.0
                    and x.x1 < gr - body * 1.0 and len(x.text) <= 36)

        i = 0
        while i < len(usable):
            if centered(usable[i]):
                j = i + 1
                while j < len(usable) and centered(usable[j]) \
                        and 0 <= (usable[j].y0 - usable[j - 1].y1) < body * 1.6:
                    j += 1
                if j - i >= 2:
                    poetic = all(not _sentence_end(x.text) for x in usable[i:j])
                    kind = "verse" if poetic else "quote"
                    for k in range(i, j):
                        usable[k].kind = kind
                i = j
            else:
                i += 1


def _deduce_title(pages: list[_Page], body: float) -> str:
    """前三页里显著大于正文且居中的短行，视为书名。"""
    for pg in pages[:3]:
        for l in pg.lines[:12]:
            if l.kind in ("header", "foot"):
                continue
            if l.size >= body * 1.5 and 1 < len(l.text) <= 40 \
                    and not PAGENUM_RE.match(l.text):
                return l.text.strip(" :：·—-")
    return ""


# ── 段落重组 ────────────────────────────────────────────────────────
def _line_height(lines: list[Line]) -> float:
    ds = []
    for a, b in zip(lines, lines[1:]):
        if a.page == b.page and a.col == b.col:
            d = b.y0 - a.y0
            if 3 < d < 200:
                ds.append(d)
    if not ds:
        return 14.0
    ds.sort()
    return ds[len(ds) // 2]


def _edges(lines: list[Line]) -> tuple[float, float]:
    if not lines:
        return 0.0, 0.0
    xs0 = sorted(l.x0 for l in lines)
    xs1 = sorted(l.x1 for l in lines)
    return xs0[max(0, int(len(xs0) * 0.10))], xs1[min(len(xs1) - 1, int(len(xs1) * 0.88))]



def _merge_into_paragraphs(pages: list[_Page], body: float) -> tuple[list[list[Line]], dict]:
    """把行合并成段落。返回段落列表 + 合并统计。"""
    seq: list[Line] = [l for pg in pages for l in pg.lines if l.kind != "front"]
    # 全局阅读顺序：页 → 栏 → 纵坐标
    seq.sort(key=lambda l: (l.page, l.col, l.y0, l.x0))
    for i, l in enumerate(seq):
        l.seq = i

    body_lines = [l for l in seq
                  if l.kind not in ("header", "head", "sub", "image", "foot")]
    lh = _line_height(body_lines)
    left, right = _edges(body_lines)
    # 双栏时每栏有自己的版心；用全局 right 会让左栏每一行都显得「不满行」，
    # 段落会被逐行切碎，跨页续接也全部失效。
    per_group: dict[tuple[int, int], tuple[float, float]] = {}
    for key in {(l.page, l.col) for l in body_lines}:
        group = [l for l in body_lines if l.page == key[0] and l.col == key[1]]
        if len(group) >= 3:
            per_group[key] = _edges(group)

    def edges(l: Line) -> tuple[float, float]:
        return per_group.get((l.page, l.col), (left, right))

    singles = ("head", "sub", "image", "caption")
    paras: list[list[Line]] = []
    buf: list[Line] = []

    def flush():
        if buf:
            paras.append(buf[:])
            buf.clear()

    prev: Line | None = None
    for l in seq:
        if l.kind == "header":
            flush()
            prev = None
            continue
        if l.kind in singles:
            flush()
            paras.append([l])
            prev = None
            continue
        if not buf:
            buf.append(l)
            prev = l
            continue
        assert prev is not None
        same_page = l.page == prev.page and l.col == prev.col
        gap = (l.y0 - prev.y1) if same_page else 0.0
        indent = l.x0 - edges(l)[0]
        short = edges(prev)[1] - prev.x1
        brk = False
        same_kind = l.kind == prev.kind and l.kind in ("quote", "verse")
        if same_kind:
            brk = False                                  # 题词 / 诗句按行保留
        elif not same_page:
            # 段落允许跨页续接：仅当上一页末行明显不满行，或本页首行缩进且
            # 上一行以句末标点收尾时才断段，否则每页开头都会碎出一段。
            brk = short > body * 1.5 or (indent > body * 1.0 and _sentence_end(prev.text))
        else:
            if gap > lh * 0.62:
                brk = True
            elif indent > body * 1.0:
                brk = True
            elif short > body * 1.6 and _sentence_end(prev.text) and gap > 0:
                brk = True
        if l.kind in ("list", "olist", "foot"):
            brk = True                                   # 新列表项 / 新脚注
        elif prev.kind in ("list", "olist", "foot") and l.kind != prev.kind \
                and gap <= lh * 0.85:
            # 列表项的续行常有悬挂缩进，不能靠 indent 判断；
            # 上一行断在句中（不以句末标点收尾）就是同一项的延续。
            if not _sentence_end(prev.text) or short < body * 1.0:
                brk = False
        if brk:
            flush()
        buf.append(l)
        prev = l
    flush()
    stats = {
        "lines": len(seq),
        "paragraphs": len(paras),
        "merged_lines": sum(len(p) for p in paras if len(p) > 1),
        "line_height": round(lh, 1),
        "left_edge": round(left, 1),
        "right_edge": round(right, 1),
    }
    return paras, stats



def _foot_ref(text: str, foot_ids: set[str]) -> str:
    """行尾孤立数字 = 脚注引用（例如「……边角料。1」）。"""
    m = re.search(r"([。！？…」』])\s*(\d{1,2})\s*$", text)
    if m and m.group(2) in foot_ids:
        return text[:m.start(2)] + f"[^{int(m.group(2))}]"
    return text


def _join(paras: list[list[Line]], body: float, foot_ids: set[str]) -> str:
    """段落内按语言合并行；生成 Markdown。"""
    out: list[str] = []
    for grp in paras:
        if not grp:
            continue
        l0 = grp[0]
        if l0.kind == "image":
            out.append(f"\n![{clean_text(l0.caption or '插图')}]({l0.src})\n")
            continue
        if l0.kind in ("head", "sub"):
            lvl = 1 + max(1, l0.head_level)          # 1 → ##, 2 → ###
            out.append("\n" + "#" * lvl + " " + l0.text + "\n")
            continue
        if l0.kind == "foot":
            continue                                 # 脚注定义统一放文末

        texts = [_foot_ref(l.text, foot_ids) for l in grp if l.text.strip()]
        if not texts:
            continue
        if l0.kind == "quote":
            # 题词 / 引文里被版面折断的行要接回去（诗行则保留，见下）
            merged: list[str] = []
            for t in texts:
                if merged and not _sentence_end(merged[-1]) and not t.startswith("——"):
                    merged[-1] += t
                else:
                    merged.append(t)
            out.append("\n" + "\n".join("> " + t for t in merged) + "\n")
            continue
        if l0.kind == "verse":
            out.append("\n::: verse\n" + "\n".join(texts) + "\n:::\n")
            continue

        cjk = _is_cjk("".join(texts)[:200])
        parts: list[str] = []
        for t in texts:
            if parts:
                last = parts[-1]
                if cjk:
                    if re.search(r"[A-Za-z0-9]$", last) and re.match(r"^[A-Za-z0-9]", t):
                        parts.append(" ")
                    elif last.endswith("-") and re.match(r"^[a-z]", t):
                        parts[-1] = last[:-1]        # 英文断词 hyphen
                else:
                    if last.endswith("-") and re.match(r"^[a-z]", t):
                        parts[-1] = last[:-1]
                    elif not last.endswith(" "):
                        parts.append(" ")
            parts.append(t)
        text = "".join(parts).strip()
        if not text:
            continue

        first = grp[0]
        if len(grp) == 1 and first.href:
            text = f"[{text}]({first.href})"
        elif len(grp) == 1 and first.italic and re.search(r"[A-Za-z]{2,}", text):
            text = f"*{text}*"

        if l0.kind == "list":
            text = "- " + BULLET_RE.sub("", text).strip()
        elif l0.kind == "olist":
            text = re.sub(r"^\s*(?:[（(])?\d{1,3}[.)、．]?[）)]?\s*", "1. ", text, count=1)
        out.append("\n" + text + "\n")
    return "\n".join(out)



def _collect_footnotes(paras: list[list[Line]]) -> tuple[list[str], set[str]]:
    """页底小字号编号行 → Markdown 脚注定义；返回可被引用的编号集合。"""
    circle = {c: str(i) for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩", 1)}
    circle["⓪"] = "0"
    notes: list[str] = []
    ids: set[str] = set()
    for grp in paras:
        if not grp or grp[0].kind != "foot":
            continue
        text = " ".join(l.text for l in grp).strip()
        m = FOOT_MARK.match(text)
        label = next((g for g in m.groups() if g), None) if m else None
        label = circle.get(label, label)
        key = str(int(label)) if label and label.isdigit() else str(len(notes) + 1)
        while key in ids:
            key += "b"
        body = text[m.end():].strip() if m else text
        if not body:
            continue
        notes.append(f"[^{key}]: {body}")
        ids.add(key)
    return notes, ids


# ── 主入口 ──────────────────────────────────────────────────────────
def extract_pdf(path: str | Path, *, assets_dir: str | Path | None = None,
                title: str = "", author: str = "",
                keep_headers: bool = False) -> Extracted:
    """把一个 PDF 重建为可重排的结构化 Markdown。"""
    if pymupdf is None:
        raise PDFError("缺少 PyMuPDF，无法读取 PDF。请先安装：pip install pymupdf")
    p = Path(path)
    if not p.exists():
        raise PDFError(f"找不到 PDF：{p}")
    if p.suffix.lower() != ".pdf":
        raise PDFError(f"不是 PDF 文件：{p.name}")
    try:
        doc = pymupdf.open(p)
    except Exception as e:
        raise PDFError(f"PDF 打不开：{e}") from e
    if doc.needs_pass and not doc.authenticate(""):
        raise PDFError("PDF 已加密，需要密码；请先去掉口令再转换。")

    n_pages = len(doc)
    if n_pages == 0:
        raise PDFError("PDF 里没有页面。")
    if n_pages > 3000:
        raise PDFError(f"PDF 有 {n_pages} 页，超出单次处理上限，建议先拆分。")

    pages: list[_Page] = []
    raw_chars = 0
    raw_empty_pages: list[int] = []
    vertical_pages = 0
    for i in range(n_pages):
        pg = doc[i]
        lines, images = _read_lines(doc, i)
        page = _Page(no=i, w=float(pg.rect.width), h=float(pg.rect.height),
                     lines=lines, images=images)
        n_chars = sum(len(l.text) for l in lines)
        raw_chars += n_chars
        if n_chars < 24:
            raw_empty_pages.append(i + 1)
        pages.append(page)
        # 竖排粗判：行宽窄、行数多且每行字数少
        if len(lines) >= 12 and sum(1 for l in lines if len(l.text) <= 3) > len(lines) * 0.7:
            vertical_pages += 1

    if len(raw_empty_pages) == n_pages:
        raise PDFError(
            "这份 PDF 没有文本层（应为扫描件或纯图片）。直接抽取只会得到空书。\n"
            "请先做 OCR：ABBYY FineReader / PaddleOCR / ocrmypdf 均可，"
            "例如 `ocrmypdf -l chi_sim+eng input.pdf ocr.pdf`，再把 ocr.pdf 传进来。")

    # 元数据
    md = {}
    try:
        md = doc.metadata or {}
    except Exception:
        pass
    auto_title = clean_text(str(md.get("title") or ""))
    if re.search(r"untitled|microsoft word|\.doc|\.pdf|^文档\d*$", auto_title, re.I):
        auto_title = ""
    meta = {
        "title": title or auto_title,
        "author": author or clean_text(str(md.get("author") or "")),
        "desc": clean_text(str(md.get("subject") or "")),
        "subject": clean_text(str(md.get("keywords") or "")).replace(";", ","),
    }

    all_lines = [l for pg in pages for l in pg.lines]
    body = _body_size(all_lines)
    for pg in pages:
        _detect_columns(pg, body)
    dropped = 0 if keep_headers else _mark_headers(pages)

    outline = _outline_map(doc)
    if not meta["title"]:
        meta["title"] = _deduce_title(pages, body)
    front_pages, toc_pages = _mark_frontmatter(pages, outline, meta)
    hits = _apply_outline(pages, outline, body)
    chapters = _classify(pages, body, hits, len(outline))
    doc.close()

    # 图片落盘，插进阅读流
    a_dir = Path(assets_dir) if assets_dir else p.parent / (p.stem + "_assets")
    rel_dir = a_dir.name
    assets: list[str] = []
    n_img = 0
    for pg in pages:
        for im in pg.images:
            data = im.get("image")
            bbox = im.get("bbox")
            if not data or not bbox or pg.front:
                continue
            if (im.get("width") or 0) < 48 or (im.get("height") or 0) < 48:
                continue                       # 装饰线 / 小图标
            x0, y0, x1, y1 = [float(v) for v in bbox]
            if (x1 - x0) > pg.w * 0.72 and (y1 - y0) > pg.h * 0.72:
                continue                       # 整页封面 / 底纹
            ext = "." + str(im.get("ext") or "png").lower().lstrip(".")
            if ext not in IMG_EXT:
                ext = ".png"
            n_img += 1
            a_dir.mkdir(parents=True, exist_ok=True)
            name = f"fig{n_img:03d}{ext}"
            (a_dir / name).write_bytes(data)
            assets.append(str(a_dir / name))
            pg.lines.append(Line(text="", x0=x0, y0=y0, x1=x1, y1=y1,
                                 size=body, bold=False, italic=False, page=pg.no,
                                 col=0 if not pg.two_col or (x0 + x1) / 2 < pg.split else 1,
                                 kind="image", src=f"{rel_dir}/{name}"))

    # 脚注里的行不再进正文流
    paras, merge_stats = _merge_into_paragraphs(pages, body)
    notes, foot_ids = _collect_footnotes(paras)
    body_md = _join(paras, body, foot_ids)
    n_notes = len(notes)

    # frontmatter（书名/作者让用户可覆盖；不写死版式，交给命令行或默认值）
    fm = ["---"]
    if meta["title"]:
        fm.append("书名：" + meta["title"].replace(":", "："))
    if meta["author"]:
        fm.append("作者：" + meta["author"].replace(":", "："))
    if meta["desc"]:
        fm.append("简介：" + meta["desc"][:120].replace(":", "：").replace("\n", " "))
    if meta["subject"]:
        fm.append("主题：" + meta["subject"][:120].replace(":", "："))
    fm.append("---")
    head = "\n".join(fm) if len(fm) > 2 else ""
    markdown = head + "\n" + body_md.strip() + "\n"
    if notes:
        markdown += "\n\n" + "\n".join(notes) + "\n"
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"
    # 相邻的列表项不该被空行拆成多个列表（否则每项之间会多出段距）
    markdown = re.sub(r"(^- [^\n]*\n)\n(?=- )", r"\1", markdown, flags=re.M)
    markdown = re.sub(r"(^\d+\. [^\n]*\n)\n(?=\d+\. )", r"\1", markdown, flags=re.M)

    # 质量诊断
    md_clean = re.sub(r"^---\n.*?\n---\n", "", markdown, flags=re.S)
    md_clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md_clean)
    md_clean = re.sub(r"^\[\^\d+\]:.*$", "", md_clean, flags=re.M)
    md_clean = re.sub(r"\[\^?[^\]]*\]", "", md_clean)
    strip = re.compile(r"[#*>`_\-\s]")
    md_clean = strip.sub("", md_clean)
    pdf_clean = strip.sub("", "".join(
        l.text for pg in pages for l in pg.lines
        if l.kind not in ("header", "image", "front", "foot")))
    retention = min(1.0, len(md_clean) / max(len(pdf_clean), 1))
    two_col_pages = sum(1 for pg in pages if pg.two_col)
    empty_pages = [pg.no + 1 for pg in pages
                   if pg.no + 1 in raw_empty_pages and not pg.front]
    warnings = []
    if empty_pages:
        warnings.append(f"{len(empty_pages)} 页几乎没有文本（可能是插页或扫描页）："
                        + ", ".join(map(str, empty_pages[:12])))
    if retention < 0.90:
        warnings.append(f"字符保留率仅 {retention:.0%}，可能有内容在重建中丢失，请核对抽取稿")
    if chapters == 0:
        warnings.append("未识别到章节结构（PDF 无书签且标题字号不明显），成品将只有一卷；"
                        "可在抽取稿里补 ## 章标题后重跑")
    if vertical_pages > n_pages * 0.5:
        warnings.append("检测到疑似竖排文字，按横排顺序重建可能错序，请人工核对抽取稿")
    if n_pages > 400:
        warnings.append("页数较多，建议抽查中段的分章与断行")

    diag = {
        "pages": n_pages,
        "text_chars": raw_chars,
        "scanned_pages": empty_pages,
        "body_size": body,
        "two_col_pages": two_col_pages,
        "dropped_headers": dropped,
        "front_pages": front_pages,
        "toc_pages": toc_pages,
        "quotes": sum(1 for pg in pages for l in pg.lines if l.kind == "quote"),
        "verses": sum(1 for pg in pages for l in pg.lines if l.kind == "verse"),
        "outline": len(outline),
        "outline_hits": hits,
        "chapters": chapters,
        "images": n_img,
        "footnotes": n_notes,
        "links": sum(1 for pg in pages for l in pg.lines if l.href),
        "retention": round(retention, 4),
        "vertical_pages": vertical_pages,
        **merge_stats,
        "warnings": warnings,
    }
    return Extracted(markdown=markdown, diagnostics=diag, meta=meta, assets=assets)


def diagnostics_markdown(diag: dict) -> str:
    """把抽取诊断写进 report.md 的 Markdown 片段。"""
    if not diag:
        return ""
    rows = [
        ("页数 / 文本层字符", f"{diag['pages']} 页 / {diag['text_chars']:,} 字"),
        ("正文字号（推断）", f"{diag['body_size']} pt"),
        ("内容保留率", f"{diag['retention']:.1%}"),
        ("栏式", f"双栏 {diag['two_col_pages']} 页" if diag['two_col_pages'] else "单栏"),
        ("剔除前置页 / 目录页", f"{diag.get('front_pages', 0)} / {diag.get('toc_pages', 0)} 页"),
        ("剔除页眉页脚", f"{diag['dropped_headers']} 行"),
        ("题词 / 诗行", f"{diag.get('quotes', 0)} / {diag.get('verses', 0)} 行"),
        ("PDF 书签 / 命中", f"{diag['outline']} / {diag['outline_hits']}"),
        ("识别章节 / 插图 / 脚注 / 链接",
         f"{diag['chapters']} / {diag['images']} / {diag['footnotes']} / {diag['links']}"),
        ("行 → 段落", f"{diag['lines']} 行 → {diag['paragraphs']} 段"
                    f"（合并 {diag['merged_lines']} 行）"),
    ]
    lines = ["### PDF 重建诊断", "", "| 项 | 值 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    if diag.get("warnings"):
        lines += ["", "**需要留意**"] + [f"- {w}" for w in diag["warnings"]]
    return "\n".join(lines)


def diagnose_only(path: str | Path) -> dict:
    """只做体检：图片落临时目录，供 GUI 预览与 `pdf2md --check` 使用。"""
    if pymupdf is None:
        return {"error": "缺少 PyMuPDF（pip install pymupdf）"}
    import tempfile
    try:
        ex = extract_pdf(path, assets_dir=Path(tempfile.gettempdir()) / "sees-press-pdf-diag")
    except PDFError as e:
        return {"error": str(e)}
    return ex.diagnostics
