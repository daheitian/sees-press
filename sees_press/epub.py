# -*- coding: utf-8 -*-
r"""IR → 重排式 EPUB 3（语义化 XHTML）。

两条设计来源合流：
  Standard Ebooks：源目录即真相源，XHTML 打 epub:type 语义（chapter /
    epigraph / footnote / noteref / colophon / toc / bodymatter），
    manifest / spine / nav / ncx 全部自动生成，绝不手工维护成品包。
  publisher-typography：已实测的三个致命点全部固化 ——
    ① mimetype 必须包首且 ZIP_STORED；
    ② spine 的 idref 必须与 manifest 的 item id 完全一致；
    ③ toc.ncx media-type = application/x-dtbncx+xml，nav 必须 properties="nav"。
  外加固化：封面 PNG 自带标题（微信读书书架取的是 cover-image 那张图本身）、
    双目录、弹出脚注带返回链接、--publisher 留空时不输出空的 dc:publisher。
"""
from __future__ import annotations

import html
import mimetypes
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .model import Book, Block, Chapter
from .inline import parse_inline, Tok
from .styles import epub_css
from .typogrify import cjk_space

XHTML_HEAD = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="{lang}" lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="styles/core.css"/>
</head>
<body{bodytype}>
"""
XHTML_TAIL = "</body>\n</html>\n"

E = lambda s: html.escape(s or "", quote=True)


def render_inline_html(text: str, notes: dict[str, str], *, space: bool,
                       used_fns: list[str]) -> str:
    """token → 语义化 HTML。脚注 token 换成 noteref，并登记到本节的 used_fns。"""
    def rec(tokens: list[Tok]) -> str:
        out = []
        for t in tokens:
            if t.kind == "text":
                s = E(t.text)
                out.append(cjk_space(s) if space else s)
            elif t.kind == "strong":
                out.append("<strong>" + rec(t.children) + "</strong>")
            elif t.kind == "em":
                out.append("<em>" + rec(t.children) + "</em>")
            elif t.kind == "del":
                out.append("<del>" + rec(t.children) + "</del>")
            elif t.kind == "code":
                out.append("<code>" + E(t.text) + "</code>")
            elif t.kind == "link":
                href = E(t.href)
                rel = ' rel="noopener"' if href.startswith("http") else ""
                out.append(f'<a href="{href}"{rel}>' + rec(parse_inline(t.text)) + "</a>")
            elif t.kind == "fn":
                n = t.text
                used_fns.append(n)
                out.append(f'<sup class="noteref"><a epub:type="noteref" '
                           f'href="#fn{n}" id="ref{n}">{n}</a></sup>')
            elif t.kind == "br":
                out.append("<br/>")
        return "".join(out)
    return rec(parse_inline(text))


def render_block_html(b: Block, notes: dict[str, str], *, space: bool,
                      used_fns: list[str], images: dict[str, str]) -> str:
    R = lambda s: render_inline_html(s, notes, space=space, used_fns=used_fns)
    if b.kind == "p":
        cls = ' class="first"' if b.attrs.get("first") else ""
        return f"<p{cls}>{R(b.text)}</p>\n"
    if b.kind == "subhead":
        lvl = min(max(b.level, 3), 6)
        return f"<h{lvl}>{R(b.text)}</h{lvl}>\n"
    if b.kind == "quote":
        paras = "".join(f"<p>{R(x.strip())}</p>\n"
                        for x in b.text.split("\n") if x.strip())
        return f'<blockquote class="quote">{paras}</blockquote>\n'
    if b.kind in ("list", "olist"):
        tag = "ul" if b.kind == "list" else "ol"
        items = "".join(f"<li>{R(x)}</li>\n" for x in b.items)
        return f'<{tag} class="list">\n{items}</{tag}>\n'
    if b.kind == "verse":
        lines = "".join(f"<p>{R(x)}</p>\n" for x in b.text.split("\n") if x.strip())
        return f'<div class="verse">{lines}</div>\n'
    if b.kind == "figure":
        src = images.get(b.src, "")
        if not src:
            return ""
        cap = f"<figcaption>{R(b.caption)}</figcaption>\n" if b.caption else ""
        return (f'<figure id="fig{abs(hash(b.src)) % 99999}">\n'
                f'<img src="{E(src)}" alt="{E(b.caption)}"/>\n{cap}</figure>\n')
    if b.kind == "rule":
        return '<hr class="dinkus"/>\n'
    if b.kind == "pagebreak":
        return '<div class="pagebreak" epub:type="pagebreak" style="break-before:page"/>\n'
    if b.kind == "code":
        return "<pre><code>" + E(b.text) + "</code></pre>\n"
    return ""


def render_chapter_xhtml(book: Book, c: Chapter, notes: dict[str, str], *,
                         space: bool, images: dict[str, str]) -> str:
    m = book.meta
    bodytype = ' epub:type="bodymatter chapter"' if c.level == 2 else ' epub:type="bodymatter part"'
    s = XHTML_HEAD.format(lang=m.language, title=E(c.display_title), bodytype=bodytype)
    cls = "part" if c.level == 1 else "chapter"
    s += f'<section id="{Path(c.slug).stem}" class="{cls}" epub:type="chapter">\n'
    s += '<header class="chapter-head">\n'
    tag = "h1" if c.level == 1 else "h2"
    num = f'<span class="num">{E(c.num)}</span>' if c.num else ""
    s += f'<{tag} epub:type="title">{num}{E(c.display_title)}</{tag}>\n'
    s += "</header>\n"
    if c.epigraph:
        lines = "".join(f"<p>{E(x)}</p>\n" for x in c.epigraph)
        s += f'<blockquote class="epigraph" epub:type="epigraph">{lines}</blockquote>\n'
        if c.attrib:
            s += f'<p class="attrib">{E(c.attrib)}</p>\n'

    used_fns: list[str] = []
    prev_kind = "head"
    for i, b in enumerate(c.blocks):
        b.attrs["first"] = (i == 0 or prev_kind in ("subhead", "quote", "figure", "list", "olist", "rule"))
        s += render_block_html(b, notes, space=space, used_fns=used_fns, images=images)
        prev_kind = b.kind

    # 本节脚注（epub:type=footnote，可弹出，带返回链接）
    for n in dict.fromkeys(used_fns):
        text = render_inline_html(notes.get(n, ""), notes, space=space, used_fns=[])
        s += (f'<aside class="footnote" epub:type="footnote" id="fn{n}">\n'
              f'<p>{n}. {text} '
              f'<a class="backref" href="#ref{n}" aria-label="返回正文">↩</a></p>\n'
              f'</aside>\n')
    s += "</section>\n" + XHTML_TAIL
    return s


def _front_xhtml(book: Book, kind: str) -> str:
    m = book.meta
    bt = {"titlepage": ' epub:type="titlepage"',
          "colophon": ' epub:type="copyright-page"',
          "cover": ' epub:type="cover"'}.get(kind, "")
    s = XHTML_HEAD.format(lang=m.language, title=E(m.title), bodytype=bt)
    if kind == "cover":
        s += ('<section class="front" epub:type="cover">\n'
              '<img src="images/cover.png" alt="封面"/>\n</section>\n')
    elif kind == "titlepage":
        sub = f'<p class="title-sub">{E(m.subtitle)}</p>' if m.subtitle else ""
        author = f'<p class="title-author">{E(m.author_line() or m.author)}</p>' if (m.author_line() or m.author) else ""
        pub = f'<p class="title-pub">{E(m.publisher)}</p>' if m.publisher else ""
        s += ('<section class="front" epub:type="titlepage">\n'
              f'<h1 class="title-main">{E(m.title)}</h1>\n{sub}{author}{pub}\n</section>\n')
    else:
        rows = [("书名", m.title), ("副题", m.subtitle), ("作者", m.author_line() or m.author),
                ("出版", m.publisher), ("编者", m.editor), ("版次", m.edition),
                ("ISBN", m.isbn), ("简介", m.desc)]
        rows = [(k, v) for k, v in rows if v]
        body = "".join(f"<p><span>{E(k)}</span>　{E(v)}</p>\n" for k, v in rows)
        s += (f'<section class="colophon" epub:type="copyright-page">\n'
              f'<h2>版权信息</h2>\n{body}'
              f'<p>　</p><p>本书由 见室印制所 SEES PRESS 自动排版 · {E(m.date)}</p>\n'
              f'</section>\n')
    return s + XHTML_TAIL


def _nav_xhtml(book: Book) -> str:
    m = book.meta
    items = []
    for c in book.chapters:
        cls = "part" if c.level == 1 else "sec"
        items.append(f'<li class="{cls}"><a href="{E(c.slug)}">{E(c.toc_title)}</a></li>')
    s = XHTML_HEAD.format(lang=m.language, title="目录", bodytype="")
    s += ('<nav epub:type="toc" id="toc">\n<h1>目录</h1>\n<ol>\n'
          + "\n".join(items) + "\n</ol>\n</nav>\n")
    s += ('<nav epub:type="landmarks" hidden="hidden">\n<h2>导览</h2>\n<ol>\n'
          '<li><a epub:type="cover" href="cover.xhtml">封面</a></li>\n'
          '<li><a epub:type="titlepage" href="titlepage.xhtml">书名页</a></li>\n'
          '<li><a epub:type="toc" href="nav.xhtml">目录</a></li>\n'
          '<li><a epub:type="bodymatter" href="' + E(book.chapters[0].slug) + '">正文</a></li>\n'
          '</ol>\n</nav>\n')
    return s + XHTML_TAIL


def _ncx(book: Book) -> str:
    m = book.meta
    navpoints = []
    for i, c in enumerate(book.chapters, 1):
        navpoints.append(
            f'<navPoint id="nav{i}" playOrder="{i}">\n'
            f'<navLabel><text>{xml_escape(c.toc_title)}</text></navLabel>\n'
            f'<content src="{xml_escape(c.slug)}"/>\n</navPoint>')
    return ("""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head>
<meta name="dtb:uid" content="%s"/>
<meta name="dtb:depth" content="2"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>%s</text></docTitle>
<navMap>
%s
</navMap>
</ncx>
""" % (xml_escape(m.identifier), xml_escape(m.title), "\n".join(navpoints)))


def _opf(book: Book, files: list[tuple[str, str, str, str]]) -> str:
    """files: [(id, href, media-type, properties)]"""
    m = book.meta
    md = [
        f'<dc:identifier id="bookid">{xml_escape(m.identifier)}</dc:identifier>',
        f'<dc:title id="title">{xml_escape(m.title)}</dc:title>',
    ]
    if m.subtitle:
        md.append(f'<dc:title id="subtitle">{xml_escape(m.subtitle)}</dc:title>')
        md.append('<meta property="title-type" refines="#subtitle">subtitle</meta>')
    if m.author:
        md.append(f'<dc:creator id="creator">{xml_escape(m.author)}</dc:creator>')
        md.append('<meta property="role" refines="#creator" scheme="marc:relators">aut</meta>')
    if m.translator:
        md.append(f'<dc:contributor id="translator">{xml_escape(m.translator)}</dc:contributor>')
        md.append('<meta property="role" refines="#translator" scheme="marc:relators">trl</meta>')
    md.append(f'<dc:language>{xml_escape(m.language)}</dc:language>')
    if m.publisher:                       # 空元素不输出（不专业）
        md.append(f'<dc:publisher>{xml_escape(m.publisher)}</dc:publisher>')
    if m.desc:
        md.append(f'<dc:description>{xml_escape(m.desc)}</dc:description>')
    if m.rights:
        md.append(f'<dc:rights>{xml_escape(m.rights)}</dc:rights>')
    if m.date:
        md.append(f'<dc:date>{xml_escape(m.date)}</dc:date>')
    for subj in [x.strip() for x in m.subject.split(",") if x.strip()]:
        md.append(f'<dc:subject>{xml_escape(subj)}</dc:subject>')
    md.append(f'<meta property="dcterms:modified">{xml_escape(m.modified)}</meta>')
    md.append('<meta name="cover" content="cover-image"/>')

    manifest = "\n".join(
        f'<item id="{i}" href="{xml_escape(h)}" media-type="{mt}"'
        + (f' properties="{pr}"' if pr else "") + "/>"
        for i, h, mt, pr in files)
    spine_ids = ["cover_xhtml", "titlepage_xhtml"] + [
        Path(c.slug).stem for c in book.chapters] + ["colophon_xhtml"]
    spine = "\n".join(f'<itemref idref="{x}"/>' for x in spine_ids)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{"".join(chr(10) + x for x in md)}
</metadata>
<manifest>
{manifest}
</manifest>
<spine toc="ncx">
{spine}
</spine>
</package>
"""


CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def build_epub(book: Book, out: str | Path, *, cover: str | Path | None = None,
               work: str | Path | None = None, cjk_spacing: bool = True) -> Path:
    """生成 EPUB 3，返回路径。images 映射由 work 目录下的 figs 自动收集。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work) if work else out.parent
    notes = {str(n.seq): n.text for n in book.notes}

    # 收集插图：统一改名 fig001.ext，杜绝中文/空格路径与 manifest 死链
    images: dict[str, str] = {}
    fig_payloads: dict[str, bytes] = {}
    n = 0
    for c in book.chapters:
        for b in c.blocks:
            if b.kind != "figure" or b.src in images:
                continue
            p = Path(b.src)
            if not p.exists():
                continue
            n += 1
            ext = p.suffix.lower() or ".png"
            name = f"fig{n:03d}{ext}"
            images[b.src] = "images/" + name
            fig_payloads[name] = p.read_bytes()

    def media(name: str) -> str:
        return mimetypes.guess_type(name)[0] or "application/octet-stream"

    cover_png = None
    if cover and Path(cover).exists():
        cover_png = Path(cover).read_bytes()
    elif (work / "cover.png").exists():
        cover_png = (work / "cover.png").read_bytes()
    if cover_png is None:
        from .cover import make_cover
        cover_png = make_cover(book.meta, fmt="png")

    entries: dict[str, bytes | str] = {}
    files: list[tuple[str, str, str, str]] = [
        ("nav_xhtml", "nav.xhtml", "application/xhtml+xml", "nav"),
        ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
        ("css", "styles/core.css", "text/css", ""),
        ("cover_image", "images/cover.png", "image/png", "cover-image"),
        ("cover_xhtml", "cover.xhtml", "application/xhtml+xml", ""),
        ("titlepage_xhtml", "titlepage.xhtml", "application/xhtml+xml", ""),
        ("colophon_xhtml", "colophon.xhtml", "application/xhtml+xml", ""),
    ]
    entries["OEBPS/nav.xhtml"] = _nav_xhtml(book).encode("utf-8")
    entries["OEBPS/toc.ncx"] = _ncx(book).encode("utf-8")
    entries["OEBPS/styles/core.css"] = epub_css(book.meta.style).encode("utf-8")
    entries["OEBPS/images/cover.png"] = cover_png
    entries["OEBPS/cover.xhtml"] = _front_xhtml(book, "cover").encode("utf-8")
    entries["OEBPS/titlepage.xhtml"] = _front_xhtml(book, "titlepage").encode("utf-8")
    entries["OEBPS/colophon.xhtml"] = _front_xhtml(book, "colophon").encode("utf-8")
    for name, data in fig_payloads.items():
        entries[f"OEBPS/images/{name}"] = data
        files.append((Path(name).stem, "images/" + name, media(name), ""))

    for c in book.chapters:
        xhtml = render_chapter_xhtml(book, c, notes, space=cjk_spacing, images=images)
        entries[f"OEBPS/{c.slug}"] = xhtml.encode("utf-8")
        files.append((Path(c.slug).stem, c.slug, "application/xhtml+xml", ""))

    entries["OEBPS/content.opf"] = _opf(book, files).encode("utf-8")
    entries["META-INF/container.xml"] = CONTAINER.encode("utf-8")

    with zipfile.ZipFile(out, "w") as z:
        # 致命点 ①：mimetype 必须是包内第一个条目且未压缩
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, b"application/epub+zip")
        for name, data in entries.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return out
