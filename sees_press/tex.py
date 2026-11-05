# -*- coding: utf-8 -*-
r"""IR → LaTeX 正文源 + 可编译整书模板。

产出两份文件，分别服务两条互不覆盖的管线（沿用 publisher-typography 的黄金法则）：
  body.tex  纯语义正文源，用 \dukpart / \duksec / \bnote 分节，
            兼容既有 scripts/tex_to_epub.py，可直接接回旧流程。
  book.tex  自带导言区的完整 XeLaTeX 文档 → 封面/书名页/版权页/目录/正文 PDF。

已固化的实测坑（详见 publisher-typography SKILL.md）：
  * \sec 是 LaTeX 内置正割函数 → 一律 \duksec 前缀
  * 满版封面用 tikz overlay，不用 \newgeometry（会多寄一张空白页）
  * \tableofcontents 自身会出「目录」标题，不另写 \chapter*{目录}
  * \rule / \includegraphics 前必须 \noindent，否则稳定溢出 2em(≈21pt)
  * 凡显式 \fontsize 必须配 \linespread{1.0}，否则全局行距会乘上去
  * 封面/前置页用 \thispagestyle{empty} + \clearpage，页码 = 物理页
  * 中文之间的空格会被 XeCJK 吃掉，信息分隔用 \quad / \enspace
  * 图片用 figure[!tb] 浮动体，不用不可分页的 minipage
  * URL 中的 % # & $ { } 必须先转义，否则吃掉外层宏的右括号
"""
from __future__ import annotations

import re
from pathlib import Path

from .model import Book, Block, Chapter
from .inline import parse_inline, Tok, FN_OPEN
from .styles import get

LATEX_SPECIAL = {
    "\\": r"\textbackslash{}", "%": r"\%", "#": r"\#", "&": r"\&",
    "$": r"\$", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
# ≥ ≤ ≈ ≠ 等 Latin Modern 缺字形（实测会同时造成整行溢出）
UNICODE_FALLBACK = {
    "≥": r"$\geq$", "≤": r"$\leq$", "≈": r"$\approx$", "≠": r"$\neq$",
    "β": r"$\beta$", "γ": r"$\gamma$", "ρ": r"$\rho$", "µ": r"$\mu$",
    "¹": r"$^1$", "²": r"$^2$", "³": r"$^3$", "⁻": r"$^{-}$",
}


ESPACE_TOKEN = "\ue020"          # 内部哨兵，避免 re 替换串里的反斜杠转义地狱


def tex_escape(s: str, cjk_spacing: bool = True) -> str:
    r"""汉字之间的空格会被 XeCJK 吃掉（实测坑），转成显式 \enspace。"""
    if cjk_spacing:
        s = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])",
                   lambda _m: ESPACE_TOKEN, s)
    out = []
    for ch in s:
        if ch == ESPACE_TOKEN:
            out.append(r"\enspace ")
        elif ch in UNICODE_FALLBACK:
            out.append(UNICODE_FALLBACK[ch])
        elif ch in LATEX_SPECIAL:
            out.append(LATEX_SPECIAL[ch])
        else:
            out.append(ch)
    return "".join(out)


def url_escape(u: str) -> str:
    r"""URL 交给 \url/\href 前先转义危险字符——% 会吃掉外层宏的 }。"""
    return (u.replace("\\", "\\\\").replace("%", r"\%").replace("#", r"\#")
             .replace("&", r"\&").replace("$", r"\$")
             .replace("{", r"\{").replace("}", r"\}").replace("_", r"\_"))


def render_inline_tex(text: str, notes: dict[str, str]) -> str:
    """token → LaTeX。脚注 token 全部消费成 \bnote{...}。"""
    def rec(tokens: list[Tok]) -> str:
        parts = []
        for t in tokens:
            if t.kind == "text":
                parts.append(tex_escape(t.text))
            elif t.kind == "strong":
                parts.append(r"\textbf{" + rec(t.children) + "}")
            elif t.kind == "em":
                parts.append(r"\emph{" + rec(t.children) + "}")
            elif t.kind == "del":
                parts.append(r"\sout{" + rec(t.children) + "}")
            elif t.kind == "code":
                parts.append(r"\texttt{" + tex_escape(t.text, cjk_spacing=False) + "}")
            elif t.kind == "link":
                href = url_escape(t.href)
                label = rec(parse_inline(t.text))
                parts.append(r"\href{" + href + "}{" + label + "}")
            elif t.kind == "fn":
                parts.append(r"\bnote{" + rec(parse_inline(notes.get(t.text, ""))) + "}")
            elif t.kind == "br":
                parts.append(r"\linebreak{}")
        return "".join(parts)
    return rec(parse_inline(text))


def _lines_tex(text: str) -> str:
    return r" \\ ".join(tex_escape(x.strip()) for x in text.split("\n") if x.strip())


def render_block_tex(b: Block, notes: dict[str, str], for_pdf: bool = False) -> str:
    R = lambda s: render_inline_tex(s, notes)
    if b.kind == "p":
        return R(b.text) + "\n"
    if b.kind == "subhead":
        return (r"\duksub{" + tex_escape(b.text) + "}\n")
    if b.kind == "quote":
        paras = [R(x.strip()) for x in b.text.split("\n") if x.strip()]
        return "\\begin{quote}\n" + "\n\n".join(paras) + "\n\\end{quote}\n"
    if b.kind in ("list", "olist"):
        env = "itemize" if b.kind == "list" else "enumerate"
        items = "\n".join(r"\item " + R(x) for x in b.items)
        return f"\\begin{{{env}}}\n{items}\n\\end{{{env}}}\n"
    if b.kind == "verse":
        lines = [tex_escape(x) for x in b.text.split("\n") if x.strip()]
        joiner = r" \\ " if for_pdf else "\n"
        return (r"\dukverse{" + joiner.join(lines) + "}\n")
    if b.kind == "figure":
        name = Path(b.src).name
        return (r"\dukfig{" + name + "}{" + tex_escape(b.caption) + "}\n")
    if b.kind == "rule":
        return "\\begin{center}\\par\\noindent\\rule{24mm}{0.4pt}\\par\\end{center}\n"
    if b.kind == "pagebreak":
        return "\\clearpage\n"
    if b.kind == "code":
        return ("\\begin{verbatim}\n" + b.text + "\n\\end{verbatim}\n")
    return ""


def render_body(book: Book, for_pdf: bool = False) -> str:
    r"""生成纯语义正文源。

    for_pdf=False → body.tex，与既有 tex_to_epub.py 契约完全一致（诗文/题词用真实换行）。
    for_pdf=True  → book_body.tex，LaTeX 用 \\ 显式分行，避免 tabular 单行溢出。
    两份都从同一 IR 派生，PDF 与 EPUB 管线互不覆盖。
    """
    notes = {str(n.seq): n.text for n in book.notes}
    out = [
        "% 由 见室印制所 SEES PRESS 自动生成 —— 纯正文源，唯一真相源之 PDF/兼容出口",
        f"% 书名：{book.meta.title}　版式：{book.meta.style}",
        "% 语法：\\dukpart{编号}{标题} / \\duksec{编号}{标题} / \\bnote{脚注}",
        "% 可直接喂给 publisher-typography/scripts/tex_to_epub.py 重出 EPUB",
        "",
    ]
    for c in book.chapters:
        macro = "dukpart" if c.level == 1 else "duksec"
        out.append(f"\\{macro}{{{tex_escape(c.num)}}}{{{tex_escape(c.display_title)}}}")
        if c.epigraph:
            elines = [tex_escape(x) for x in c.epigraph]
            ejoin = r" \\ " if for_pdf else "\n"
            out.append(r"\dukepigraph{" + ejoin.join(elines) + "}")
        if c.attrib:
            out.append(r"\dukattrib{" + tex_escape(c.attrib) + "}")
        out.append("")
        for b in c.blocks:
            out.append(render_block_tex(b, notes, for_pdf=for_pdf))
        out.append("")
    return "\n".join(out)


PREAMBLE = r"""%% 见室印制所 SEES PRESS · 自动生成的整书模板
\documentclass[UTF8,zihao=5,openany]{ctexart}
\usepackage[paperwidth=@@PW@@mm,paperheight=@@PH@@mm,left=@@ML@@mm,right=@@MR@@mm,top=@@MT@@mm,bottom=@@MB@@mm]{geometry}
\usepackage{titlesec,fancyhdr,enumitem,xcolor,graphicx}
\usepackage{hyperref,xurl}
\usepackage{tikz}
% 只用 TinyTeX 基础安装里确定存在的包；可选效果一律 \providecommand 降级，
% 不让「本机少一个 .sty」导致整份编译失败
\providecommand{\sout}[1]{#1}
\defaultfontfeatures{Ligatures=TeX}
\definecolor{ink}{HTML}{@@INK@@}
\definecolor{accent}{HTML}{@@ACC@@}
\color{ink}
\linespread{@@LEAD@@}
\sloppy
\setlength{\emergencystretch}{3em}
\setlength{\parindent}{2em}
\setlength{\parskip}{0pt}
\graphicspath{{figs/}{./}}
\hypersetup{colorlinks=true,linkcolor=ink,urlcolor=accent,breaklinks=true,pdftitle={@@TITLE@@},pdfauthor={@@AUTHOR@@}}
% 罗马数字/带圈数字按全角处理，避免 Latin Modern 缺字形（ctex 未加载时静默跳过）
\ifcsname xeCJKDeclareCharClass\endcsname
  \xeCJKDeclareCharClass{CJK}{"00B5,"2109,"2160,"2161,"2162,"2163,"2164,"2165,%
    "2460,"2461,"2462,"2463,"2464,"2465,"2466,"2467,"2468,"2469}%
\fi
% 显式字号必须配 \linespread{1.0}，否则被全局行距乘上去
\newcommand{\FSbody}{\linespread{1.0}\fontsize{10.5pt}{@@BODYLEAD@@pt}\selectfont}
\AtBeginDocument{\FSbody}
% 页眉页脚
\pagestyle{fancy}\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancyfoot[C]{\zihao{-5}\color{accent}\thepage}
@@HEADER@@
% 语义宏：正文源只描述结构，外观全在这里
\newcommand{\bnote}[1]{\footnote{#1}}
\newcommand{\dukpart}[2]{%
  \clearpage\thispagestyle{empty}\null\vspace*{@@PARTVSPACE@@}%
  \begin{center}\noindent
  \if\relax\detokenize{#1}\relax\else{\color{accent}\zihao{-4}#1\par}\vspace{8mm}\fi
  {\heiti\zihao{2}\color{ink}#2\par}
  \end{center}
  \vspace{14mm}\noindent{\color{accent}\rule{\linewidth}{0.4pt}}\par\vspace{12mm}
  \addcontentsline{toc}{part}{#1\enspace#2}\markboth{#2}{}%
}
\newcommand{\duksec}[2]{%
  @@SECNEWPAGE@@\noindent
  \if\relax\detokenize{#1}\relax\else{\color{accent}\zihao{-4}#1\par}\vspace{3mm}\fi
  {\heiti\zihao{3}\color{ink}#2\par}\vspace{9mm}
  \addcontentsline{toc}{section}{#1\enspace#2}\markright{#2}\par\nobreak%
}
\newcommand{\duksub}[1]{\par\vspace{6mm}\noindent{\heiti\zihao{4}\color{accent}#1}\par\nobreak\vspace{2mm}}
\newcommand{\dukepigraph}[1]{\begin{quote}\kaishu\zihao{-4}\color{accent}#1\end{quote}}
\newcommand{\dukattrib}[1]{\begin{flushright}\zihao{-5}\color{accent}#1\end{flushright}}
\newcommand{\dukquote}[1]{\begin{quote}\kaishu\small #1\end{quote}}
\newcommand{\dukverse}[1]{\begin{center}\kaishu\begin{tabular}{l}#1\end{tabular}\end{center}}
\newcommand{\dukfig}[2]{%
  \begin{figure}[!tb]\centering
  \includegraphics[width=.86\linewidth,height=.62\textheight,keepaspectratio]{#1}
  \if\relax\detokenize{#2}\relax\else\caption{\zihao{-5}\color{accent}#2}\fi
  \end{figure}%
}
\renewcommand{\contentsname}{目录}
\setcounter{tocdepth}{2}
\setcounter{secnumdepth}{0}
\begin{document}
"""

FULL_DOC = r"""%% ---- 满版封面（tikz overlay，不用 \newgeometry）----
\thispagestyle{empty}
\IfFileExists{@@COVER@@}{%
\begin{tikzpicture}[remember picture,overlay]
\node[inner sep=0pt,anchor=north west] at (current page.north west)
  {\includegraphics[width=\paperwidth,height=\paperheight,keepaspectratio=false]{@@COVER@@}};
\end{tikzpicture}%
}{}
\clearpage
%% ---- 书名页 ----
\thispagestyle{empty}
\begin{center}
\vspace*{34mm}
{\color{accent}\noindent\rule{16mm}{0.4pt}}\par\vspace{14mm}
{\heiti\zihao{2}\color{ink}@@TITLE@@}\par
\if\relax\detokenize{@@SUBTITLE@@}\relax\else\vspace{5mm}{\songti\zihao{-4}\color{accent}@@SUBTITLE@@}\par\fi
\vspace{30mm}
{\kaishu\zihao{4}@@AUTHORLINE@@}\par
\vspace*{20mm}
{\zihao{-5}\color{accent}@@PUBLISHER@@}\par
\end{center}
\clearpage
%% ---- 版权页 ----
\thispagestyle{empty}
\vspace*{16mm}
\begin{center}{\heiti\zihao{4}版权信息}\end{center}
\vspace{8mm}
{\zihao{-5}\color{accent}\noindent
书名\quad @@TITLE@@\par
\if\relax\detokenize{@@SUBTITLE@@}\relax\else 副题\quad @@SUBTITLE@@\par\fi
作者\quad @@AUTHORLINE@@\par
\if\relax\detokenize{@@PUBLISHER@@}\relax\else 出品\quad @@PUBLISHER@@\par\fi
\if\relax\detokenize{@@EDITOR@@}\relax\else 编者\quad @@EDITOR@@\par\fi
版次\quad @@EDITION@@\par
\if\relax\detokenize{@@ISBN@@}\relax\else ISBN\quad @@ISBN@@\par\fi
\par\vspace{10mm}
\noindent @@DESC@@\par
}
\vfill
{\zihao{-5}\color{accent}\begin{center}本书由 见室印制所 SEES PRESS 自动排版\quad ·\quad @@DATE@@\end{center}}
\clearpage
%% ---- 目录（\tableofcontents 自带标题，切勿再手写 \chapter*{目录}）----
\thispagestyle{empty}
\tableofcontents
\clearpage
%% ---- 正文 ----
\pagestyle{fancy}
\input{@@BODYNAME@@}
\end{document}
"""


def render_book_tex(book: Book, *, cover: str = "cover.png",
                    body_name: str = "body", chapter_newpage: bool = True) -> str:
    s = get(book.meta.style)
    pw, ph = s["paper"]
    ml, mr, mt, mb = s["margins"]
    header = ""
    if s["header"]:
        header = (r"\fancyhead[LE]{\zihao{-5}\color{accent}\nouppercase{\leftmark}}" "\n"
                  r"\fancyhead[RO]{\zihao{-5}\color{accent}\nouppercase{\rightmark}}" "\n"
                  r"\renewcommand{\headrulewidth}{0.2pt}")
    pre = (PREAMBLE
           .replace("@@PW@@", str(pw)).replace("@@PH@@", str(ph))
           .replace("@@ML@@", str(ml)).replace("@@MR@@", str(mr))
           .replace("@@MT@@", str(mt)).replace("@@MB@@", str(mb))
           .replace("@@INK@@", s["ink"].lstrip("#"))
           .replace("@@ACC@@", s["accent"].lstrip("#"))
           .replace("@@LEAD@@", str(s["leading"]))
           .replace("@@BODYLEAD@@", f"{10.5 * 1.6:.1f}")
           .replace("@@PARTVSPACE@@", s["chapter_vspace"].replace("mm", "mm"))
           .replace("@@HEADER@@", header)
           .replace("@@SECNEWPAGE@@", r"\clearpage" if chapter_newpage else r"\par\bigskip")
           .replace("@@TITLE@@", tex_escape(book.meta.title))
           .replace("@@AUTHOR@@", tex_escape(book.meta.author)))

    m = book.meta
    esc = tex_escape
    doc = (FULL_DOC
           .replace("@@COVER@@", cover)
           .replace("@@TITLE@@", esc(m.title))
           .replace("@@SUBTITLE@@", esc(m.subtitle))
           .replace("@@AUTHORLINE@@", esc(m.author_line()) or "~")
           .replace("@@PUBLISHER@@", esc(m.publisher))
           .replace("@@EDITOR@@", esc(m.editor))
           .replace("@@EDITION@@", esc(m.edition or f"{m.date} 第 1 版"))
           .replace("@@ISBN@@", esc(m.isbn))
           .replace("@@DESC@@", esc(m.desc[:180]))
           .replace("@@DATE@@", esc(m.date))
           .replace("@@BODYNAME@@", body_name))
    return pre + doc
