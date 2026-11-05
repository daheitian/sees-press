# -*- coding: utf-8 -*-
r"""输入归一化：把任何来源变成一段干净的 UTF-8 纯文本 + 推断出的书目元数据。

支持：直接粘贴的文本、.md/.txt/.markdown、.html/.htm、.docx、URL、
以及一个目录（按文件名排序合并，方便「一个章节一个 md」的整书稿）。

注意本机环境：Windows 注册表里残留了失效的系统代理 127.0.0.1:7897，
urllib 会自动读到它导致 TLS 握手失败。所有联网请求一律显式
ProxyHandler({}) 绕过，不能依赖默认 opener。
"""
from __future__ import annotations

import os
import re
import io
import zipfile
import html as _html
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Tuple

from .model import Meta

TEXT_EXT = {".md", ".markdown", ".txt", ".text", ".org", ".rst"}
HTML_EXT = {".html", ".htm", ".xhtml"}

FRONTMATTER_KEYS = {
    "title": "title", "书名": "title", "标题": "title", "name": "title",
    "subtitle": "subtitle", "副题": "subtitle", "副标题": "subtitle",
    "author": "author", "作者": "author", "by": "author",
    "region": "region", "国别": "region",
    "translator": "translator", "译者": "translator",
    "publisher": "publisher", "出版": "publisher", "出品": "publisher", "出版方": "publisher",
    "editor": "editor", "编者": "editor", "编辑": "editor",
    "edition": "edition", "版次": "edition",
    "isbn": "isbn",
    "identifier": "identifier",
    "desc": "desc", "简介": "desc", "description": "desc", "摘要": "desc",
    "subject": "subject", "主题": "subject", "标签": "subject", "tags": "subject",
    "language": "language", "语言": "language",
    "style": "style", "版式": "style", "风格": "style",
    "series": "series", "系列": "series", "丛书": "series",
    "cover": "cover", "封面": "cover",
}


class _TextExtractor(HTMLParser):
    """极简 Readability：只保留正文标签，跳过 script/style/nav/aside/header/footer。"""
    SKIP = {"script", "style", "nav", "aside", "header", "footer", "form", "noscript", "svg"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK and not self.skip_depth:
            self.parts.append("\n")
        elif tag == "img" and not self.skip_depth:
            d = dict(attrs)
            alt, src = d.get("alt", ""), d.get("src", "")
            if src:
                self.parts.append(f"\n![{alt}]({src})\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK and not self.skip_depth:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def _from_docx(path: Path) -> str:
    """不依赖 python-docx，直接读 OOXML。保留标题层级与段落。"""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    out = []
    for p in root.iter(ns + "p"):
        text = "".join(t.text or "" for t in p.iter(ns + "t"))
        if not text.strip():
            out.append("")
            continue
        style = ""
        ppr = p.find(ns + "pPr")
        if ppr is not None:
            ps = ppr.find(ns + "pStyle")
            if ps is not None:
                style = ps.get(ns + "val") or ""
        m = re.search(r"[Hh]eading(\d)|标题\s*(\d)", style)
        if m:
            lvl = int(m.group(1) or m.group(2))
            out.append("#" * min(lvl, 6) + " " + text)
        else:
            out.append(text)
    return "\n\n".join(out)


def _from_pdf(path: Path) -> str:
    """PDF → 可重排的结构化 Markdown；插图落到 PDF 同目录，供 IR 解析时定位。"""
    from .pdfextract import extract_pdf
    ex = extract_pdf(path, assets_dir=path.parent / (path.stem + "_assets"))
    return ex.markdown


def _from_html(raw: str) -> str:
    p = _TextExtractor()
    p.feed(raw)
    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    title = _html.unescape(title_m.group(1)).strip() if title_m else ""
    body = p.text()
    if title and not body.startswith("# "):
        body = "# " + title + "\n\n" + body
    return body


def fetch_url(url: str, timeout: int = 20) -> str:
    """联网必绕系统代理（见模块说明）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"),
        ("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"),
    ]
    with opener.open(url, timeout=timeout) as r:
        data = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    raw = data.decode(charset, errors="replace")
    ctype = r.headers.get_content_type() or ""
    if "html" in ctype or raw.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        return _from_html(raw)
    return raw


def read_source(src: str | os.PathLike, base: str | os.PathLike | None = None) -> Tuple[str, str]:
    """返回 (文本, 来源显示名)。src 可以是路径 / URL / 直接就是正文。"""
    s = str(src).strip()
    if re.match(r"^https?://", s, re.I):
        return fetch_url(s), s

    p = Path(s)
    if not p.is_absolute() and base:
        q = Path(base) / p
        if q.exists():
            p = q
    if p.is_dir():
        files = sorted([f for f in p.iterdir()
                        if f.suffix.lower() in TEXT_EXT | HTML_EXT | {".docx"}],
                       key=lambda x: x.name)
        if not files:
            raise ValueError(f"目录里没有可用文稿：{p}")
        chunks = []
        for f in files:
            t, _ = read_source(f)
            chunks.append(t)
            chunks.append("\n\n")
        return "".join(chunks), p.name + f"/（{len(files)} 个文件）"
    if not p.exists():
        # 不是路径，就当用户直接粘贴了正文
        if len(s) > 40 or "\n" in s:
            return s, "粘贴内容"
        raise FileNotFoundError(f"找不到输入：{s}")

    suffix = p.suffix.lower()
    if suffix == ".docx":
        return _from_docx(p), p.name
    if suffix == ".pdf":
        return _from_pdf(p), p.name
    raw = p.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    if suffix in HTML_EXT:
        return _from_html(text), p.name
    return text.replace("\r\n", "\n").replace("\r", "\n"), p.name


def split_frontmatter(text: str) -> Tuple[dict, str]:
    """解析 YAML-ish frontmatter 或开头的「书名：X」中文键值块。"""
    data: dict[str, str] = {}
    body = text
    m = re.match(r"^\ufeff?---+\s*\n(.*?)\n---+\s*\n?", text, re.S)
    block = ""
    if m:
        block, body = m.group(1), text[m.end():]
    else:
        # 开头连续若干行「键：值」
        lines = text.split("\n")
        picked = []
        for ln in lines:
            mm = re.match(r"^\s*([A-Za-z\u4e00-\u9fa5]{1,10})\s*[:：]\s*(.+?)\s*$", ln)
            if mm and mm.group(1).lower() in FRONTMATTER_KEYS:
                picked.append(ln)
            elif ln.strip() == "" and picked:
                continue
            else:
                break
        if picked:
            block = "\n".join(picked)
            body = "\n".join(lines[len(picked):]).lstrip("\n")
    for ln in block.split("\n"):
        mm = re.match(r"^\s*([\w\u4e00-\u9fa5]+)\s*[:：]\s*(.*?)\s*$", ln)
        if not mm:
            continue
        k = FRONTMATTER_KEYS.get(mm.group(1).lower())
        if k:
            v = mm.group(2).strip().strip('"').strip("'").strip("[]")
            data[k] = v
    return data, body


def merge_meta(meta: Meta, fm: dict, text: str) -> Meta:
    """frontmatter > 用户显式传入 > 从正文推断。"""
    for k, v in fm.items():
        if hasattr(meta, k) and v and not getattr(meta, k):
            setattr(meta, k, v)
    if not meta.title or meta.title == "未命名":
        m = re.search(r"^\s*#\s+(.+?)\s*$", text, re.M)
        if m:
            meta.title = m.group(1).strip().strip("#").strip()
    if not meta.author:
        m = re.search(r"^\s*(?:作者|文|by)\s*[:：]\s*(.+?)\s*$", text[:1200], re.M | re.I)
        if m:
            meta.author = m.group(1).strip()
    if not meta.desc:
        # 取正文第一段作简介（去掉 markdown 标记）
        for para in re.split(r"\n\s*\n", text):
            p = para.strip()
            if p.startswith("#") or len(p) < 30:
                continue
            meta.desc = re.sub(r"[*_`>\[\]()!]", "", p)[:110]
            break
    return meta.finalise()
