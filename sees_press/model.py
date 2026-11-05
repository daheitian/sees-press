# -*- coding: utf-8 -*-
r"""书稿中间表示（IR）。

设计原则：IR 是唯一真相源。所有出口（EPUB / PDF / 预览 / TeX）都从它派生，
彼此互不覆盖，也不从 PDF 反推 EPUB（分页产物转重排会碎）。

这套思路与 Standard Ebooks 的 source directory 一致：一份语义化源 +
可重复构建 + 自动生成的 manifest / spine / toc，而不是手工维护成品包。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class Meta:
    """书目元数据。用户不给就自动推断 / 留安全占位，绝不生成空的 OPF 元素。"""
    title: str = "未命名"
    subtitle: str = ""
    author: str = ""
    region: str = ""            # 〔美〕之类，用于书名页
    translator: str = ""
    publisher: str = ""
    editor: str = ""
    edition: str = ""
    isbn: str = ""
    identifier: str = ""
    desc: str = ""
    subject: str = ""           # 逗号分隔
    language: str = "zh-CN"
    rights: str = ""
    date: str = ""
    style: str = "疏朗留白"
    series: str = ""
    modified: str = ""

    def finalise(self) -> "Meta":
        if not self.title.strip():
            self.title = "未命名"
        if not self.identifier.strip():
            self.identifier = ("urn:isbn:" + self.isbn) if self.isbn else "urn:uuid:" + str(uuid.uuid4())
        if not self.modified.strip():
            self.modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.date.strip():
            self.date = datetime.now().strftime("%Y-%m-%d")
        return self

    def author_line(self) -> str:
        """书名页署名：〔美〕某某 著。"""
        who = self.author.strip()
        if not who:
            return ""
        region = f"〔{self.region}〕" if self.region.strip() else ""
        tail = "　著" if not self.translator.strip() else f"　著　{self.translator.strip()}　译"
        return region + who + tail


@dataclass
class Block:
    """一个正文块。kind 决定语义，渲染器决定外观。"""
    kind: str = "p"
    # p / subhead / quote / list / olist / verse / figure / rule / code / table / epigraph /
    # attrib / plain / dedication / pagebreak
    text: str = ""
    level: int = 3              # subhead 用：3 = h3
    items: list[str] = field(default_factory=list)
    src: str = ""               # figure 的图片路径 / URL
    caption: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.text.strip() and not self.items and not self.src


@dataclass
class Note:
    """脚注。epub:type="footnote" 可弹出，PDF 侧转 \footnote。"""
    key: str
    text: str
    seq: int = 0


@dataclass
class Chapter:
    """part = 篇（一级），sec = 章/节（二级）。编号与标题分存，便于目录与篇页排版。"""
    num: str = ""
    title: str = ""
    level: int = 2              # 1 = part, 2 = sec
    epigraph: list[str] = field(default_factory=list)
    attrib: str = ""
    blocks: list[Block] = field(default_factory=list)
    slug: str = ""

    @property
    def display_title(self) -> str:
        """篇页只有编号没有标题时，编号顶上作主标题（否则目录会出现空白标题）。"""
        return self.title.strip() or self.num.strip() or "无题"

    @property
    def toc_title(self) -> str:
        parts = [p for p in (self.num.strip(), self.title.strip()) if p]
        return "　".join(parts) if len(parts) == 2 else (parts[0] if parts else "无题")

    def word_count(self) -> int:
        n = sum(len(b.text) + sum(len(i) for i in b.items) for b in self.blocks)
        return n


@dataclass
class Issue:
    """质检条目。level: error / warn / info。"""
    level: str
    code: str
    msg: str
    where: str = ""

    def line(self) -> str:
        icon = {"error": "✗", "warn": "!", "info": "·"}.get(self.level, "·")
        loc = f"　[{self.where}]" if self.where else ""
        return f"- {icon} **{self.code}**{loc}　{self.msg}"


@dataclass
class Book:
    """整部书稿。"""
    meta: Meta = field(default_factory=Meta)
    chapters: list[Chapter] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    source_name: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def word_count(self) -> int:
        return sum(c.word_count() for c in self.chapters)

    def toc(self) -> list[tuple[int, Chapter]]:
        return [(i, c) for i, c in enumerate(self.chapters)]

    def to_dict(self) -> dict:
        return asdict(self)

    def add(self, level: str, code: str, msg: str, where: str = "") -> None:
        self.issues.append(Issue(level, code, msg, where))
