# -*- coding: utf-8 -*-
r"""把净化后的文本解析成 Book（IR）。

对应 Standard Ebooks 的 `se create-draft` + `se split-file` + `se semanticate`：
自动识别篇章结构、给内容打语义标签（epigraph / verse / footnote / figure），
再由渲染器决定外观。目录、manifest、spine 全部由此派生，不手工维护。

章节识别支持两种稿件来源：
  Markdown  # / ## / ###
  纯文本    第一章 / 一、 / 卷一 / 序、前言、附录……
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .model import Book, Block, Chapter, Meta, Note
from .inline import FN_OPEN, FN_CLOSE
from .ingest import split_frontmatter, merge_meta
from .typogrify import typogrify, is_cjk_text, find_unusual, check_consistency

CN_NUM = "零〇一二三四五六七八九十百千两0-9０-９"
FRONT_WORDS = {"序章", "序言", "自序", "序", "前言", "导言", "导论", "绪论", "引子", "楔子"}
BACK_WORDS = {"后记", "跋", "尾声", "附录", "参考文献", "注释", "致谢", "代跋"}
HEAD_PREFIX = re.compile(
    rf"^(第[{CN_NUM}]+[章节篇部卷回讲]|卷[{CN_NUM}]+|[{CN_NUM}]{{1,3}}、)\s*(.*)$"
)
RULE_RE = re.compile(r"^\s*(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})\s*$")
IMG_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
HEAD_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
END_PUNCT = ("。", "！", "？", "，", "；", "：", "…", ".", "!", "?")


def _same_title(a: str, b: str) -> bool:
    a, b = re.sub(r"\s+", "", a or ""), re.sub(r"\s+", "", b or "")
    return bool(a and b and (a == b or (len(a) > 2 and (a in b or b in a))))


def _slug(i: int, level: int) -> str:
    return f"{'part' if level == 1 else 'sec'}{i:03d}.xhtml"


def _chapter_from_heading(line: str) -> tuple[int, str, str] | None:
    """返回 (level, num, title)；level=0 表示是节内小标题。"""
    m = HEAD_RE.match(line)
    if m:
        lvl = len(m.group(1))
        if lvl <= 2:
            title = m.group(2).strip()
            mm = HEAD_PREFIX.match(title)      # 「## 第一章　开端」→ 编号 + 标题
            if mm and 0 < len(mm.group(2).strip()) <= 40:
                return (1 if lvl == 1 else 2, mm.group(1).strip(), mm.group(2).strip())
            return (1 if lvl == 1 else 2, "", title)
        return (0, "", "")
    s = line.strip()
    if not s or len(s) > 46 or s.endswith(END_PUNCT):
        return None
    m = HEAD_PREFIX.match(s)
    if m:
        num, title = m.group(1).strip(), m.group(2).strip()
        if len(title) <= 40:
            return (2, num, title)
    for word in sorted(FRONT_WORDS | BACK_WORDS, key=len, reverse=True):
        if s == word or s.startswith((word + " ", word + "：", word + ":", word + "　")):
            return (2, word, s[len(word):].lstrip("：:　 "))
    return None


def _extract_footnotes(text: str) -> tuple[str, list[Note]]:
    """收集 `[^key]: 内容`（支持缩进续行），引用处换成私有区 token。"""
    defs: dict[str, str] = {}
    lines = text.split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^\[\^([^\]]+)\]:\s*(.*)$", lines[i])
        if not m:
            kept.append(lines[i])
            i += 1
            continue
        key, body = m.group(1).strip(), m.group(2)
        i += 1
        while i < len(lines) and (lines[i].startswith("    ") or lines[i].startswith("\t")):
            body += " " + lines[i].strip()
            i += 1
        defs[key] = body.strip()
    text = "\n".join(kept)
    text = re.sub(r"\[\^([^\]]+)\]",
                  lambda m: f"{FN_OPEN}fn:{m.group(1)}{FN_CLOSE}", text)
    notes = [Note(k, v, n + 1) for n, (k, v) in enumerate(defs.items())]
    return text, notes


def _renumber_tokens(text: str, notes: list[Note]) -> tuple[str, set[str]]:
    """把脚注 token 的 key 换成顺序号；返回未定义引用的集合。"""
    lut = {n.key: str(n.seq) for n in notes}
    missing: set[str] = set()

    def sub(m):
        k = m.group(1)
        if k not in lut:
            missing.add(k)
            return m.group(0)
        return f"{FN_OPEN}fn:{lut[k]}{FN_CLOSE}"

    return re.sub(FN_OPEN + r"fn:([^" + FN_CLOSE + r"]+)" + FN_CLOSE, sub, text), missing


def _flush_para(buf: list[str], blocks: list[Block], cjk: bool) -> None:
    if not buf:
        return
    parts: list[str] = []
    for ln in buf:
        ln = ln.strip()
        if not ln:
            continue
        if parts and not cjk and not re.search(r"[-—–]$", parts[-1]):
            parts.append(" ")
        parts.append(ln)
    text = "".join(parts).strip()
    if text:
        blocks.append(Block("p", text))
    buf.clear()


def parse_book(raw: str, meta: Meta | None = None, *,
               quote_style: str = "auto", base_dir: str | Path | None = None,
               source_name: str = "") -> Book:
    """主入口：原始文本 → 完整 Book（含 issues）。"""
    meta = meta or Meta()
    fm, body = split_frontmatter(raw)
    # 已显式给书名时，所有 H1 都是「篇」；否则第一个 H1 视作书名
    explicit_title = bool(meta.title and meta.title != "未命名")
    meta = merge_meta(meta, fm, body)
    book = Book(meta=meta, source_name=source_name)

    # 1) 排版净化（先保护 Markdown 结构，再改标点）
    clean = typogrify(body, quote_style=quote_style)
    cjk = is_cjk_text(clean)

    # 2) 脚注
    clean, notes = _extract_footnotes(clean)
    clean, missing = _renumber_tokens(clean, notes)
    book.notes = notes
    for k in sorted(missing):
        book.add("warn", "脚注未定义", f"引用了 [{k}] 但文中没有对应定义", "脚注")

    # 3) 逐行切块
    lines = clean.split("\n")
    chapters: list[Chapter] = []
    cur: Chapter | None = None
    para: list[str] = []
    list_kind, list_items = "", []
    quote_buf: list[str] = []
    code_buf: list[str] | None = None
    first_h1_used = False

    def ensure_chapter():
        nonlocal cur
        if cur is None:
            cur = Chapter(num="", title=meta.title, level=2, slug=_slug(1, 2))
            chapters.append(cur)
        return cur

    def flush_quote():
        if quote_buf:
            text = "\n".join(x.strip() for x in quote_buf if x.strip())
            if text:
                ensure_chapter().blocks.append(Block("quote", text))
            quote_buf.clear()

    def flush_list():
        nonlocal list_kind, list_items
        if list_items:
            ensure_chapter().blocks.append(
                Block(list_kind, items=[x.strip() for x in list_items]))
            list_kind, list_items = "", []

    def flush_all():
        # 有实际内容才建默认章，避免开头空行凭空造出空章
        if para or quote_buf or list_items:
            _flush_para(para, ensure_chapter().blocks, cjk)
        else:
            _flush_para(para, [], cjk)
        flush_quote()
        flush_list()

    def new_chapter(level: int, num: str, title: str):
        nonlocal cur
        flush_all()
        idx = len(chapters) + 1
        cur = Chapter(num=num, title=title, level=level, slug=_slug(idx, level))
        chapters.append(cur)

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 围栏代码
        if code_buf is not None:
            if stripped.startswith("```"):
                ensure_chapter().blocks.append(Block("code", "\n".join(code_buf)))
                code_buf = None
            else:
                code_buf.append(line)
            i += 1
            continue
        if stripped.startswith("```"):
            flush_all()
            code_buf = []
            i += 1
            continue

        # 显式 verse / epigraph 块
        vm = re.match(r"^:::\s*(verse|epigraph)\s*$", stripped, re.I)
        if vm:
            flush_all()
            mode = vm.group(1).lower()
            i += 1
            vbuf: list[str] = []
            while i < len(lines) and lines[i].strip() != ":::":
                vbuf.append(lines[i].strip())
                i += 1
            if mode == "verse":
                ensure_chapter().blocks.append(Block("verse", "\n".join(vbuf)))
            else:
                ensure_chapter().epigraph = [x for x in vbuf if x]
            i += 1
            continue

        # 标题
        hm = HEAD_RE.match(line)
        if hm and len(hm.group(1)) == 1 and not first_h1_used:
            h1 = hm.group(2).strip()
            at_start = not (chapters or para or quote_buf or list_items)
            # 首行 H1：未给书名时视作书名；给了书名且与之重复时也跳过，避免多出空章
            if at_start and (not explicit_title or _same_title(h1, meta.title)):
                first_h1_used = True
                if not meta.title or meta.title == "未命名":
                    meta.title = h1
                i += 1
                continue
        ch = _chapter_from_heading(line)
        if ch and ch[0] in (1, 2):
            new_chapter(ch[0], ch[1], ch[2])
            i += 1
            continue
        if hm and len(hm.group(1)) >= 3:
            flush_all()
            ensure_chapter().blocks.append(
                Block("subhead", hm.group(2).strip(), level=len(hm.group(1))))
            i += 1
            continue

        if not stripped:                    # 空行 = 段落边界
            flush_all()
            i += 1
            continue

        if "pagebreak" in stripped.lower() and stripped.startswith("<!--"):
            flush_all()
            ensure_chapter().blocks.append(Block("pagebreak"))
            i += 1
            continue
        if RULE_RE.match(line):
            flush_all()
            ensure_chapter().blocks.append(Block("rule"))
            i += 1
            continue

        im = IMG_RE.match(line)             # 图片
        if im:
            flush_all()
            src = im.group(2)
            if base_dir and not re.match(r"^(?:https?://|data:)", src, re.I):
                p = Path(base_dir) / src
                if p.exists():
                    src = str(p.resolve())
            ensure_chapter().blocks.append(
                Block("figure", src=src, caption=im.group(1).strip()))
            i += 1
            continue

        qm = QUOTE_RE.match(line)           # 引文（连续 > 行合并）
        if qm:
            _flush_para(para, ensure_chapter().blocks, cjk)
            flush_list()
            quote_buf.append(qm.group(1))
            i += 1
            continue
        if quote_buf:
            flush_quote()

        lm = LIST_RE.match(line)            # 列表
        if lm:
            _flush_para(para, ensure_chapter().blocks, cjk)
            kind = "olist" if re.match(r"^\s*\d+[.)]\s+", line) else "list"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append(lm.group(1))
            i += 1
            continue
        if list_items:
            flush_list()

        # 题词出处：紧跟章首引文之后的「——某某」
        if (cur and cur.blocks and cur.blocks[-1].kind == "quote"
                and re.match(r"^\s*[—–]{1,2}\s*\S{1,24}\s*$", line)
                and not cur.attrib and len(cur.blocks) <= 2):
            cur.attrib = stripped
            i += 1
            continue

        para.append(line)
        i += 1

    flush_all()

    # 4) 章首引文提升为 epigraph（常见写法：> 引语 / > ——出处）
    for c in chapters:
        if c.blocks and c.blocks[0].kind == "quote" and not c.epigraph:
            q = c.blocks.pop(0)
            c.epigraph = [x.strip() for x in q.text.split("\n") if x.strip()]
            m = re.match(r"^\s*[—–]{1,2}\s*(.+)$", c.epigraph[-1]) if c.epigraph else None
            if m and len(c.epigraph) > 1:
                c.attrib = "——" + m.group(1).strip()
                c.epigraph.pop()

    # 5) 兜底：没有任何内容 → 单章
    if not chapters or not any(c.blocks or c.epigraph for c in chapters):
        blocks: list[Block] = []
        buf: list[str] = []
        for ln in clean.split("\n"):
            if ln.strip():
                buf.append(ln)
            else:
                _flush_para(buf, blocks, cjk)
        _flush_para(buf, blocks, cjk)
        chapters = [Chapter(num="", title=meta.title, level=2,
                            slug=_slug(1, 2), blocks=blocks or [Block("p", "")])]

    book.chapters = chapters
    for idx, c in enumerate(chapters, 1):
        c.slug = _slug(idx, c.level)

    book.stats = {
        "chapters": len(chapters),
        "parts": sum(1 for c in chapters if c.level == 1),
        "words": book.word_count(),
        "notes": len(notes),
        "figures": sum(1 for c in chapters for b in c.blocks if b.kind == "figure"),
        "cjk": cjk,
    }
    _lint_structure(book, clean)
    return book


def _lint_structure(book: Book, clean: str) -> None:
    """结构层质检（产物层质检见 lint.py）。"""
    for idx, c in enumerate(book.chapters, 1):
        where = f"第{idx}章 {c.display_title}"
        if not c.blocks and not c.epigraph:
            book.add("warn", "空章节", "这一章没有任何正文块", where)
        if len(c.display_title) > 46:
            book.add("warn", "标题过长", "可能把正文误判成了标题", where)
        lv = [b.level for b in c.blocks if b.kind == "subhead"]
        for a, b in zip(lv, lv[1:]):
            if b - a > 1:
                book.add("info", "标题层级跳跃", f"h{a} 直接跳到 h{b}", where)
                break
        first_para = next((b for b in c.blocks if b.kind == "p"), None)
        if first_para and len(first_para.text) < 12 and len(c.blocks) > 2:
            book.add("info", "疑似残留短行", "章首有一段特别短，检查是否断行错误", where)
    for t, n in Counter(c.display_title for c in book.chapters).items():
        if n > 1:
            book.add("warn", "重复章名", f"「{t}」出现 {n} 次", "目录")
    for u, sample in find_unusual(clean):
        book.add("warn", "异常字符", f"{u}　{sample}", "全文")
    for m in check_consistency(clean):
        book.add("info", "写法不一致", m, "全文")
    if not book.meta.author:
        book.add("info", "元数据", "未指定作者，书名页将留空", "书目")
