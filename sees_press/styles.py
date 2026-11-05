# -*- coding: utf-8 -*-
"""版式风格表。

参数沿用 publisher-typography 已实测的七种中文书籍装帧标准：
开本 / 四边边距 / 行距 / 专色 / 书眉特征。PDF 走 geometry+ctex 精确分页，
EPUB 走同一组数值映射成的重排 CSS，保证两种介质气质一致。
"""
from __future__ import annotations

# paper: mm, margins: [left,right,top,bottom] mm
STYLES: dict[str, dict] = {
    "疏朗留白": {
        "id": "airy", "paper": [140, 203], "margins": [24, 24, 30, 32],
        "leading": 1.85, "ink": "#2F2F2F", "accent": "#3A3A3A",
        "cover_ink": "#30343A", "cover_paper": "#F5F1E8",
        "chapter_vspace": "38mm", "header": False, "rule": True,
        "desc": "大留白、极简无书眉、篇章首页留白式", "mood": "思想随笔 / 译文单行本",
        "font": "serif", "epub_indent": 2, "epub_size": 1.0,
    },
    "墨绿专色": {
        "id": "forest", "paper": [145, 210], "margins": [22, 18, 22, 20],
        "leading": 1.7, "ink": "#25282A", "accent": "#1F4E3D",
        "cover_ink": "#1F4E3D", "cover_paper": "#F7F5EE",
        "chapter_vspace": "30mm", "header": True, "rule": True,
        "desc": "宋正文 + 一专色提气、书眉细线", "mood": "经典 / 学术 / 精装",
        "font": "serif", "epub_indent": 2, "epub_size": 1.0,
    },
    "文艺细线": {
        "id": "line", "paper": [145, 210], "margins": [20, 20, 24, 26],
        "leading": 1.75, "ink": "#2B2B2B", "accent": "#B0522D",
        "cover_ink": "#B0522D", "cover_paper": "#FAF6EF",
        "chapter_vspace": "32mm", "header": True, "rule": True,
        "desc": "书眉 + 赭橘专色细线、明快文艺", "mood": "散文 / 游记 / 文学",
        "font": "serif", "epub_indent": 2, "epub_size": 1.0,
    },
    "窄边密排": {
        "id": "dense", "paper": [125, 185], "margins": [14, 12, 16, 18],
        "leading": 1.5, "ink": "#1A1A1A", "accent": "#1A1A1A",
        "cover_ink": "#222222", "cover_paper": "#F2F0E9",
        "chapter_vspace": "20mm", "header": True, "rule": False,
        "desc": "窄边密排、真脚注、小开本", "mood": "文库本 / 手册 / 长篇",
        "font": "serif", "epub_indent": 2, "epub_size": 0.96,
    },
    "撞色色块": {
        "id": "pop", "paper": [148, 195], "margins": [18, 30, 22, 26],
        "leading": 1.7, "ink": "#1F2E30", "accent": "#E8622D",
        "cover_ink": "#E8622D", "cover_paper": "#FBF7F0",
        "chapter_vspace": "34mm", "header": False, "rule": False,
        "desc": "不对称边距、橙 × 墨青色块篇首", "mood": "年轻 / 商业 / 图文书",
        "font": "sans", "epub_indent": 0, "epub_size": 1.0,
    },
    "素雅留白": {
        "id": "quiet", "paper": [130, 185], "margins": [24, 24, 28, 32],
        "leading": 1.9, "ink": "#333A33", "accent": "#6E7F6B",
        "cover_ink": "#4A5749", "cover_paper": "#F6F4EC",
        "chapter_vspace": "44mm", "header": False, "rule": True,
        "desc": "极疏朗、楷体引言页、留白即设计", "mood": "诗集 / 札记 / 小册",
        "font": "serif", "epub_indent": 2, "epub_size": 1.02,
    },
    "框线灰调": {
        "id": "frame", "paper": [130, 184], "margins": [19, 17, 23, 25],
        "leading": 1.65, "ink": "#2A2A2A", "accent": "#8A8A8A",
        "cover_ink": "#3E3E3E", "cover_paper": "#F4F2EB",
        "chapter_vspace": "26mm", "header": True, "rule": True,
        "desc": "本白气质、框线式篇名、灰细书眉", "mood": "古典 / 讲义 / 注释本",
        "font": "serif", "epub_indent": 2, "epub_size": 0.98,
    },
}

DEFAULT_STYLE = "疏朗留白"


def get(name: str) -> dict:
    return STYLES.get(name or DEFAULT_STYLE, STYLES[DEFAULT_STYLE])


def as_list() -> list[dict]:
    return [{"name": k, **v} for k, v in STYLES.items()]


def epub_css(style_name: str) -> str:
    """把装帧参数映射为 EPUB 3 重排式 CSS（兼容性优先）。

    微信读书 / 掌阅 / Kobo / Apple Books 的渲染内核差异极大，实测必须避开：
      · CSS 自定义属性 var() —— 旧 WebView 不支持，含它的整条声明会被丢弃；
      · `var(--x)66` 这类变量拼接 —— 本就是无效 CSS，颜色全丢；
      · 8 位 hex（#RRGGBBAA）、`list-style: "字符串"` —— 支持面很窄；
      · `-webkit-text-size-adjust: none` —— 会锁死字号，违反「文字必须可缩放」；
      · 硬指定中文字体名 —— 覆盖阅读器自带的字体设置，跨平台必然跑偏。
    因此：颜色直接内联、字号用 em、字体交还阅读器、分页属性新旧双写。
    """
    from string import Template
    s = get(style_name)
    rule = "border-bottom: 1px solid " + s["accent"] + ";" if s["rule"] else ""
    tpl = Template(r"""/* SEES PRESS · $style · EPUB 3 重排式（面向国产阅读器兼容） */
@namespace epub "http://www.idpf.org/2007/ops";
html { font-size: 100%; }
body {
  font-family: serif;
  font-size: ${size}em;
  line-height: $leading;
  color: $ink;
  margin: 0 5%;
  text-align: justify;
  text-justify: inter-ideograph;
  line-break: strict;
  word-break: normal;
  overflow-wrap: break-word;
  widows: 2; orphans: 2;
}
/* 段首缩进；紧接标题/引文/图/表/列表的首段不缩进（中文书惯例） */
p { margin: 0; text-indent: ${indent}em; widows: 2; orphans: 2; }
p.first, p.noindent, h1 + p, h2 + p, h3 + p,
blockquote + p, figure + p, table + p, ul + p, ol + p { text-indent: 0; }
p.first { margin-top: .35em; }
span.cjksp { white-space: nowrap; }
/* 标题 */
h1, h2, h3 {
  font-family: sans-serif;
  font-weight: 600; text-align: left; text-indent: 0;
  color: $ink;
  page-break-after: avoid; break-after: avoid;
}
h1 { font-size: 1.55em; letter-spacing: .06em; margin: 0 0 1.2em; }
h2 { font-size: 1.32em; letter-spacing: .04em; margin: 0 0 1em; }
h3 { font-size: 1.08em; margin: 1.6em 0 .6em; color: $accent; }
h1 .num, h2 .num {
  display: block; font-size: .58em; letter-spacing: .22em;
  color: $accent; font-weight: 400; margin-bottom: .5em;
}
/* 篇章首页 · 分页属性新旧双写 */
section.chapter, section.part { page-break-before: always; break-before: page; }
div.pagebreak { page-break-before: always; break-before: page; }
header.chapter-head { $rule padding-bottom: .8em; margin-bottom: 1.8em; }
section.part header.chapter-head { margin-top: 22%; }
/* 题词 / 引文 */
blockquote.epigraph {
  margin: 2.2em 8% 2.2em 12%; font-size: .9em; line-height: 1.7;
  color: $accent; text-indent: 0; text-align: left;
}
blockquote.epigraph p { text-indent: 0; margin: 0; }
p.attrib { text-align: right; text-indent: 0; font-size: .85em; color: $accent; margin: 0 8% 2em 0; }
blockquote.quote {
  margin: 1.1em 0 1.1em 1.4em; padding-left: .9em;
  border-left: 2px solid $accent; text-indent: 0;
}
blockquote.quote p { text-indent: 0; }
/* 列表：不用自定义 list-style 字符串，改用广泛支持的生成内容 */
ul, ol { margin: .7em 0 .9em 1.2em; padding-left: .8em; }
li { margin: .18em 0; text-indent: 0; }
ul li { list-style-type: none; }
ul li:before { content: "—　"; margin-left: -1.2em; color: $accent; }
div.verse { margin: 1.2em 0 1.2em 1.6em; text-indent: 0; }
div.verse p { text-indent: 0; margin: 0; line-height: 1.65; }
/* 插图 */
figure { margin: 1.4em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: .82em; color: $accent; text-indent: 0; margin-top: .5em; }
/* 真表格（财务 / 数据），不再用 <pre> 假表格 */
table { width: 100%; border-collapse: collapse; margin: 1.1em 0;
        font-size: .9em; page-break-inside: avoid; }
td, th { padding: .32em .5em; border-bottom: 1px solid #cccccc;
         vertical-align: top; text-align: left; }
td.num, th.num { text-align: right; white-space: nowrap; }
caption { font-size: .82em; color: $accent; text-align: left; margin-bottom: .5em; }
/* 脚注：epub:type=footnote 可弹出，带返回链接 */
sup.noteref a { text-decoration: none; color: $accent; font-size: .72em; }
aside.footnote { font-size: .84em; margin: 1.6em 0 0;
                 border-top: 1px solid #cccccc; padding-top: .6em; }
aside.footnote p { text-indent: 0; }
a.backref { text-decoration: none; color: $accent; }
/* 分隔 / 代码 */
hr.dinkus { border: 0; text-align: center; margin: 1.8em 0; }
hr.dinkus:after { content: "*　*　*"; color: $accent; letter-spacing: .3em; }
pre { font-family: monospace; font-size: .84em; line-height: 1.45;
      white-space: pre-wrap; word-break: break-word; text-indent: 0;
      margin: 1em 0; padding: .7em; border: 1px solid #dddddd; }
code { font-family: monospace; font-size: .9em; }
/* 前置页 */
section.front { page-break-after: always; break-after: page; text-align: center; }
section.front img { max-width: 100%; height: auto; }
section.front p { text-indent: 0; }
p.title-main { font-family: sans-serif; font-size: 2.1em;
               letter-spacing: .12em; margin: 26% 0 .4em; }
p.title-sub { font-size: .92em; color: $accent; letter-spacing: .08em; margin: 0 0 3em; }
p.title-author { font-size: 1em; margin-top: 3em; }
p.title-pub { font-size: .82em; color: $accent; margin-top: 8em; }
section.colophon { font-size: .82em; color: $accent; }
section.colophon h2 { font-size: 1.1em; text-align: center; }
section.colophon p { margin: .35em 0; }
/* 目录 */
nav#toc ol { list-style: none; margin: 0; padding: 0; }
nav#toc li { margin: .55em 0; text-indent: 0; }
nav#toc li.part { margin-top: 1.2em; font-weight: 600; }
nav#toc li.sec { padding-left: 1.6em; }
nav#toc a { text-decoration: none; color: $ink; }
""")
    return tpl.substitute(style=style_name, ink=s["ink"], accent=s["accent"],
                          indent=s["epub_indent"], size=s["epub_size"],
                          leading=s["leading"], rule=rule)
