# -*- coding: utf-8 -*-
r"""单文件浏览器预览 —— 对应 Standard Ebooks 的 `se recompose-epub`。

EPUB 本身没法直接看版面，所以把包里的 XHTML 重组成一个离线 HTML5：
CSS 内联、图片转 data URI、封面内嵌，带桌面 / 手机窄屏切换与目录跳转。
用户既有 make_preview.py 会在 tools.py 里额外尝试，失败就用这份内置版。
"""
from __future__ import annotations

import base64
import html
import re
import zipfile
from pathlib import Path

BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.S | re.I)
IMG_RE = re.compile(r'src="([^"]+)"')
CSS_LINK_RE = re.compile(r'<link[^>]+rel="stylesheet"[^>]*/?>', re.I)


def _data_uri(z: zipfile.ZipFile, name: str) -> str | None:
    import mimetypes
    if name not in z.namelist():
        return None
    mt = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return f"data:{mt};base64," + base64.b64encode(z.read(name)).decode("ascii")


def build_preview(epub: str | Path, out: str | Path, title: str = "") -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub) as z:
        names = z.namelist()
        css = z.read("OEBPS/styles/core.css").decode("utf-8") if "OEBPS/styles/core.css" in names else ""
        cover = _data_uri(z, "OEBPS/images/cover.png")

        def xhtml_body(n: str) -> str:
            t = z.read(n).decode("utf-8", "replace")
            m = BODY_RE.search(t)
            t = m.group(1) if m else t
            t = CSS_LINK_RE.sub("", t)
            # 相對路径图片 → data URI
            def img_sub(mm):
                src = mm.group(1)
                if src.startswith(("data:", "http")):
                    return mm.group(0)
                target = "OEBPS/" + src.lstrip("./")
                uri = _data_uri(z, target)
                return f'src="{uri}"' if uri else mm.group(0)
            return IMG_RE.sub(img_sub, t)

        front = [n for n in ("OEBPS/cover.xhtml", "OEBPS/titlepage.xhtml") if n in names]
        chapters = [n for n in names if re.match(r"OEBPS/(?:part|sec)\d+\.xhtml$", n)]
        chapters.sort(key=lambda n: (0 if "/part" in n else 1, n))
        # 按 OPF spine 顺序更准确
        try:
            opf = z.read("OEBPS/content.opf").decode("utf-8", "replace")
            import xml.etree.ElementTree as ET
            root = ET.fromstring(opf)
            ns = {"o": "http://www.idpf.org/2007/opf"}
            manifest = {i.get("id"): i.get("href") for i in root.findall(".//o:manifest/o:item", ns)}
            spine = ["OEBPS/" + manifest[x.get("idref")]
                     for x in root.findall(".//o:spine/o:itemref", ns)
                     if x.get("idref") in manifest]
            ordered = [n for n in spine if n in names]
            if ordered:
                front = [n for n in ordered if n in front] + [n for n in ordered if n in chapters and n not in front]
                chapters = [n for n in ordered if n in chapters and n not in front]
        except Exception:
            pass

        toc = []
        for n in chapters:
            t = z.read(n).decode("utf-8", "replace")
            m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", t, re.S)
            label = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else Path(n).stem
            toc.append((Path(n).stem, html.escape(label)))

        parts = [xhtml_body(n) for n in front]
        parts.append('<div class="sp-mark">正文</div>')
        for n in chapters:
            body = xhtml_body(n)
            stem = Path(n).stem
            parts.append(f'<div class="doc" id="{stem}">{body}</div>')

    toc_html = "\n".join(f'<a href="#{s}">{t}</a>' for s, t in toc)
    cover_html = f'<img class="cover" src="{cover}" alt="封面"/>' if cover else ""
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{html.escape(title or '电子书预览')}</title>
<style>
{css}
:root {{ --bg:#f3f3f1; --card:#fff; --line:#e6e4df; --muted:#8a8a84; }}
* {{ box-sizing:border-box; }}
body {{ background:var(--bg); margin:0; padding:0 0 80px;
        font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
.toolbar {{ position:sticky; top:0; z-index:20; display:flex; gap:10px; align-items:center;
            padding:10px 20px; background:rgba(255,255,255,.88); backdrop-filter:blur(12px);
            border-bottom:1px solid var(--line); font-size:13px; }}
.toolbar b {{ font-weight:600; margin-right:auto; letter-spacing:.02em; }}
.toolbar button {{ border:1px solid var(--line); background:#fff; color:#333; border-radius:7px;
                   padding:6px 13px; cursor:pointer; font-size:12px; }}
.toolbar button:hover {{ border-color:#aaa; }}
.toolbar button.on {{ background:#2f2f2f; color:#fff; border-color:#2f2f2f; }}
.layout {{ display:flex; gap:28px; justify-content:center; align-items:flex-start;
           padding:36px 24px; max-width:1100px; margin:0 auto; }}
nav.toc {{ position:sticky; top:62px; width:210px; flex:0 0 210px; background:var(--card);
           border:1px solid var(--line); border-radius:12px; padding:18px; max-height:78vh; overflow:auto; }}
nav.toc h4 {{ margin:0 0 12px; font-size:12px; color:var(--muted); letter-spacing:.14em; font-weight:600; }}
nav.toc a {{ display:block; padding:5px 0; color:#444; text-decoration:none; font-size:12.5px;
             border-bottom:1px solid #f2f1ee; }}
nav.toc a:hover {{ color:var(--accent,#1F4E3D); }}
main {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
        width:min(720px,100%); padding:64px 72px; box-shadow:0 6px 30px rgba(0,0,0,.035); }}
main img.cover {{ display:block; width:56%; margin:0 auto 40px; box-shadow:0 8px 24px rgba(0,0,0,.13); }}
.doc {{ padding-top:8px; }}
.sp-mark {{ text-align:center; font-size:11px; letter-spacing:.4em; color:var(--muted);
            margin:52px 0 40px; }}
body section.front {{ page-break-after:always; }}
body section.chapter, body section.part {{ page-break-before:always; break-before:page; }}
body.mobile .layout {{ padding:12px; }}
body.mobile nav.toc {{ display:none; }}
body.mobile main {{ width:100%; padding:34px 26px; }}
body.mobile img.cover {{ width:72%; }}
@media print {{ .toolbar,nav.toc {{ display:none; }} main {{ border:0; box-shadow:none; padding:0; }} }}
</style></head>
<body>
<div class="toolbar"><b>{html.escape(title or '电子书预览')}</b>
<button id="btnToc">目录</button><button id="btnMobile">手机窄屏</button>
<button onclick="window.print()">打印 / 存 PDF</button></div>
<div class="layout">
<nav class="toc" id="toc"><h4>目 录</h4>{toc_html}</nav>
<main id="main">{cover_html}{''.join(parts)}</main>
</div>
<script>
document.getElementById('btnToc').onclick=function(){{
  var t=document.getElementById('toc'); var h=t.style.display==='none';
  t.style.display=h?'block':'none'; this.classList.toggle('on',!h);
}};
document.getElementById('btnMobile').onclick=function(){{
  document.body.classList.toggle('mobile'); this.classList.toggle('on',document.body.classList.contains('mobile'));
}};
</script>
</body></html>"""
    out.write_text(doc, encoding="utf-8")
    return out
