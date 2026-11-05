# -*- coding: utf-8 -*-
r"""排版净化层（typogrify）。

对应 Standard Ebooks 的 `se typogrify` / `se clean` /
`se find-unusual-characters` / `se find-mismatched-dashes` 四个工具，
但规则换成中文书籍规范：

1. 先护住 Markdown 结构再改标点 —— 这是 publisher-typography 实测踩过的坑：
   `\(...\)` → `（...）` 会把 `![图](路径)`、`[文](链接)` 的括号一起转成全角，
   图片行随即不匹配正则、解析循环死锁。这里用私有区哨兵先存起来，改完还原。
2. 智能引号、破折号、省略号、汉字邻接的半角标点、重复标点。
3. 中西文「盘古之白」由渲染器按需注入（PDF 交给 xeCJK，EPUB 注入 U+2009）。
4. 异常字符与不一致写进质检报告，只报告不擅自改内容。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
CJK_PUNCT = r"，。！？；：、「」『』（）《》〈〉【】—…"
SENT_OPEN = "\ue010"
SENT_CLOSE = "\ue011"

# 顺序敏感：先大块结构，再行内结构，最后 HTML 标签
PROTECTORS = [
    re.compile(r"```[^\n]*\n.*?```", re.S | re.M),          # 围栏代码块
    re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)"),                  # 图片
    re.compile(r"\[[^\]\n]*\]\([^)\n]*\)"),                   # 链接
    re.compile(r"`[^`\n]+`"),                                  # 行内代码
    re.compile(r"</?[A-Za-z][^>\n]*>"),                        # HTML 标签
    re.compile(r"\[\^[^\]\n]+\]"),                             # 脚注标记
]
SENT_RE = re.compile(SENT_OPEN + r"(\d+)" + SENT_CLOSE)

MOJIBAKE = ["â€™", "â€œ", "â€", "Ã©", "Ã¨", "Ã¼", "Ã±", "ï¿½", "å¤", "ä¸"]


def is_cjk_text(text: str) -> bool:
    sample = text[:4000]
    if not sample:
        return False
    cjk = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    return cjk / max(len(sample), 1) > 0.12


def _protect(text: str) -> tuple[str, list[str]]:
    keep: list[str] = []

    def stash(m):
        keep.append(m.group(0))
        return f"{SENT_OPEN}{len(keep) - 1}{SENT_CLOSE}"

    for pat in PROTECTORS:
        text = pat.sub(stash, text)
    return text, keep


def _restore(text: str, keep: list[str]) -> str:
    return SENT_RE.sub(lambda m: keep[int(m.group(1))] if int(m.group(1)) < len(keep) else "", text)


def _smart_quotes(text: str, corner: bool) -> str:
    d_open, d_close = ("「", "」") if corner else ("“", "”")
    s_open, s_close = ("『", "』") if corner else ("‘", "’")
    out, d_flag, s_flag = [], False, False
    for i, c in enumerate(text):
        if c == '"':
            out.append(d_close if d_flag else d_open)
            d_flag = not d_flag
        elif c == "'":
            # 英文缩写 don't / it's 不转换：仅当前后是空白或标点时才配对
            prev = text[i - 1] if i else " "
            nxt = text[i + 1] if i + 1 < len(text) else " "
            if prev.isalnum() and nxt.isalnum():
                out.append(c)
            else:
                out.append(s_close if s_flag else s_open)
                s_flag = not s_flag
        else:
            out.append(c)
    return "".join(out)


def _dashes_and_ellipsis(text: str, cjk: bool) -> str:
    if cjk:
        text = re.sub(r"-{2,}", "——", text)
        text = re.sub(r"(?<!—)—(?!—)", "——", text)
        text = re.sub(r"\.{3,}|\.\s\.\s\.", "……", text)
        text = re.sub(r"(?<!…)…(?!…)", "……", text)
    else:
        text = re.sub(r"-{2,}", "—", text)
        text = re.sub(r"\.{3,}|\.\s\.\s\.", "…", text)
        text = re.sub(r"\s+—\s+", " — ", text)
    return text


def _cjk_punctuation(text: str) -> str:
    # 只在紧邻汉字时把半角标点改全角，避免误伤英文里的逗号与小数点
    table = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
    for half, full in table.items():
        h = re.escape(half)
        text = re.sub(rf"(?<=[{CJK}]){h}", full, text)
        text = re.sub(rf"{h}(?=[{CJK}])", full, text)
    # 半角括号且内部含汉字 → 全角（markdown 结构已先存哨兵，不会误伤）
    text = re.sub(r"\(([^()\n]*[" + CJK + r"][^()\n]*)\)", r"（\1）", text)
    # 重复标点收敛
    text = re.sub(r"！{2,}", "！", text)
    text = re.sub(r"？{2,}", "？", text)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"[!?]{1,}！", "！", text)
    return text


def typogrify(text: str, quote_style: str = "auto", cjk_spacing: bool = False) -> str:
    """主入口。quote_style: auto | corner(「」) | curly(“”) | straight(不改)。"""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    text, keep = _protect(text)
    cjk = is_cjk_text(text)

    style = quote_style
    if style == "auto":
        style = "corner" if cjk else "curly"
    if style != "straight":
        text = _smart_quotes(text, style == "corner")
    text = _dashes_and_ellipsis(text, cjk)
    if cjk:
        text = _cjk_punctuation(text)
    # 排版净化：行尾空白、3 行以上空行、连续半角空格
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)
    # 去掉会污染成品的不可见字符（保留 \n \t 与 U+2009）
    text = "".join(c for c in text
                   if c in "\n\t\u2009" or unicodedata.category(c) not in ("Cf", "Cc"))

    text = _restore(text, keep)
    if cjk_spacing:
        text = cjk_space(text)
    return text


THIN = "\u2009"
# 数量单位前不插间距：2026年 / 第3页 / 5元 / 3.5%
_UNIT_FOLLOW = re.compile(r"^[年月日时分秒岁人次页章节篇卷版元万亿%％°]")


def cjk_space(text: str) -> str:
    r"""在汉字与西文/数字之间插入 1/5 em 细空格（盘古之白）。

    PDF 侧不要调用：XeCJK 会吃掉汉字之间的空格并自动处理中西文间距，
    重复插入反而会在 \hspace 场景里出问题。
    """
    text = re.sub(rf"(?<=[{CJK}])(?=[A-Za-z0-9])", THIN, text)
    text = re.sub(rf"(?<=[A-Za-z0-9])(?=[{CJK}])", THIN, text)
    # 数量单位紧跟数字时不留白：2026年 / 第3页 / 5元
    text = re.sub(rf"([A-Za-z0-9]){THIN}([年月日时分秒岁人次页章节篇卷版元万亿])",
                  r"\1\2", text)
    # 全角标点两侧不加
    text = re.sub(rf"([{CJK_PUNCT}])\u2009", r"\1", text)
    text = re.sub(rf"\u2009([{CJK_PUNCT}])", r"\1", text)
    return text


def find_unusual(text: str) -> list[tuple[str, str]]:
    """对应 se find-unusual-characters：返回 [(描述, 样例)]。"""
    found: list[tuple[str, str]] = []
    bad_chars: Counter = Counter()
    for c in text:
        if c in "\n\t\u2009" or "\ue000" <= c <= "\ue011":
            continue   # 本软件自己的脚注/哨兵 token，不是异常字符
        cat = unicodedata.category(c)
        if cat in ("Cc", "Cf", "Co") or c in ("\ufffd", "\u00a0"):
            bad_chars[c] += 1
    for c, n in bad_chars.items():
        try:
            name = unicodedata.name(c, "未命名")
        except ValueError:
            name = "未知"
        found.append((f"不可见/异常字符 U+{ord(c):04X} {name} ×{n}", repr(c)))
    for seq in MOJIBAKE:
        if seq in text:
            found.append((f"疑似乱码（编码转换残留）×{text.count(seq)}", seq))
    if "\ufffd" in text:
        found.append(("替换字符 U+FFFD（解码丢失）", "\ufffd"))
    return found


def check_consistency(text: str) -> list[str]:
    """对应 se find-mismatched-dashes / find-mismatched-diacritics 的中文等价物。"""
    msgs: list[str] = []
    # 同一拉丁词不同连字符写法：e-mail / email
    words: dict[str, set[str]] = {}
    for w in re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}\b", text):
        key = w.lower().replace("-", "")
        if "-" in w or key in {x.lower().replace("-", "") for x in words}:
            words.setdefault(key, set()).add(w)
    for variants in words.values():
        if len(variants) > 1 and any("-" in v for v in variants):
            msgs.append("连字符写法不一致：" + " / ".join(sorted(variants)))
    # 省略号 / 破折号混用
    if "……" in text and re.search(r"\.{3,}|。{3,}", text):
        msgs.append("省略号形式混用：…… 与 ... / 。。。")
    if "——" in text and re.search(r"(?<!—)—(?!—)", text):
        msgs.append("破折号形式混用：—— 与单个 —")
    # 直引号与弯引号混用
    if re.search(r'["\']', text) and ("「" in text or "“" in text):
        msgs.append("引号形式混用：直引号与成对的中文/弯引号同时存在")
    # 全半角标点混用（汉字邻接的半角标点应已被规范化，仍出现说明是异体来源）
    half = len(re.findall(rf"[{CJK}][,;:!?]|[,;:!?][{CJK}]", text))
    if half > 4:
        msgs.append(f"汉字邻接处仍有 {half} 处半角标点（可能来自不同来源的稿件）")
    return msgs
