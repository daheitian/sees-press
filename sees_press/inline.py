# -*- coding: utf-8 -*-
r"""行内标记解析：把轻量 Markdown 解析成中立 token 序列。

为什么不用正则替换：publisher-typography 已实测，`**a *b* c**` 这类嵌套
正则处理不了，且 html.escape 之前保不住已生成的标签。这里用逐字符扫描 +
递归，EPUB / LaTeX / 预览三个渲染器共用同一份 token，语义不会漂移。

脚注引用在 parse 阶段被换成一个私有区 token，渲染器必须全部消费掉；
lint 会检查私有区残留，避免把 \ue000 之类的怪字符印进成品书。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FN_OPEN = "\ue000"
FN_CLOSE = "\ue001"
FN_RE = re.compile(FN_OPEN + r"fn:([^" + FN_CLOSE + r"]+)" + FN_CLOSE)


@dataclass
class Tok:
    kind: str                       # text / strong / em / del / code / link / fn / br
    text: str = ""
    href: str = ""
    children: list["Tok"] = field(default_factory=list)


def _find_closing(s: str, start: int, marker: str) -> int:
    """找配对结束标记，跳过反斜杠转义。找不到返回 -1。"""
    i, n = start, len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if s.startswith(marker, i):
            return i
        i += 1
    return -1


def _markup_at(s: str, i: int) -> tuple[Tok, int] | None:
    """尝试在 i 处解析一个成对标记，返回 (token, 结束下标)。"""
    for marker, kind in (("**", "strong"), ("~~", "del"), ("__", "strong"),
                         ("*", "em"), ("_", "em")):
        if not s.startswith(marker, i) or i + len(marker) >= len(s):
            continue
        if kind == "em" and marker == "*" and s[i + 1] in " \t":
            continue
        j = _find_closing(s, i + len(marker), marker)
        if j <= i + len(marker) - 1:
            continue
        inner = s[i + len(marker):j]
        if kind == "em" and (not inner.strip() or inner.startswith(" ")):
            continue
        return Tok(kind, children=parse_inline(inner)), j + len(marker)
    return None


def parse_inline(s: str) -> list[Tok]:
    """解析一段行内文本：**粗** *斜* `码` [文](链接) ~~删~~、脚注 token、<br>。"""
    out: list[Tok] = []
    buf: list[str] = []

    def flush():
        if buf:
            out.append(Tok("text", "".join(buf)))
            buf.clear()

    i, n = 0, len(s)
    while i < n:
        c = s[i]

        if c == FN_OPEN:                       # 脚注引用
            m = FN_RE.match(s, i)
            if m:
                flush()
                out.append(Tok("fn", m.group(1)))
                i = m.end()
                continue
        if c == "\\" and i + 1 < n and s[i + 1] in r"\*_`[]{}~":   # 转义
            buf.append(s[i + 1])
            i += 2
            continue
        if c == "`":                          # 行内代码
            j = _find_closing(s, i + 1, "`")
            if j > i:
                flush()
                out.append(Tok("code", s[i + 1:j]))
                i = j + 1
                continue
        if c == "[":                          # 链接
            m = re.match(r"\[([^\]\n]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)", s[i:])
            if m:
                flush()
                out.append(Tok("link", m.group(1), m.group(2)))
                i += m.end()
                continue
        if c == "<":
            m = re.match(r"<br\s*/?>", s[i:], re.I)
            if m:
                flush()
                out.append(Tok("br"))
                i += m.end()
                continue
        found = _markup_at(s, i)
        if found:
            tok, nxt = found
            flush()
            out.append(tok)
            i = nxt
            continue

        buf.append(c)
        i += 1

    flush()
    return out


def plain(tokens: list[Tok]) -> str:
    """纯文本（用于摘要、目录、字数统计）。"""
    parts = []
    for t in tokens:
        if t.kind == "text":
            parts.append(t.text)
        elif t.kind in ("code", "fn"):
            parts.append("")
        elif t.kind == "br":
            parts.append(" ")
        else:
            parts.append(t.text or plain(t.children))
    return "".join(parts)


def has_tokens(s: str) -> bool:
    return FN_OPEN in s
