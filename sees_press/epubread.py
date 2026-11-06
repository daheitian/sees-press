# -*- coding: utf-8 -*-
r"""鲁棒读取外部 EPUB（不假设它出自 SEES PRESS）。

参照 joeseesun/kindle-push-macos 的 bookinfo.py / chunks.py：真实世界的 EPUB
工具在容器引号、OPF 路径、封面声明上互相不兼容，reader 必须 tolerant：

  - container.xml 可能用单引号或双引号（ElementTree 遇单引号会 ParseError，
    故用正则而非 XML 解析）；
  - OPF 可能不在 OEBPS/ 而在 zip 顶层，甚至被其它工具搬到子目录；
  - 封面声明有四种写法：properties="cover-image"、<meta name="cover" content=id>、
    meta 里 content 直接是文件名、<guide><reference type="cover"> 指向的页面首图。

用途：将来导入 / 校验任意来源 EPUB（如把其它排版软件出的书重新排进 SEES PRESS），
以及把 compat.py 的封面判定从「只认自家 properties」升级为「四层回退」。
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET


def opf_path(zf: zipfile.ZipFile) -> str:
    """从 container.xml 读出 OPF 路径；兼容单 / 双引号（正则，不依赖 XML 解析）。

    单引号 container 会让 ElementTree.fromstring 抛 ParseError，所以这里用正则。
    """
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", "ignore")
    except KeyError:
        opfs = [n for n in zf.namelist() if n.lower().endswith(".opf")]
        if len(opfs) == 1:
            return opfs[0]
        raise FileNotFoundError("缺少 META-INF/container.xml，且包内 OPF 不唯一")
    m = re.search(r'full-path\s*=\s*["\']([^"\']+)', container)
    if not m:
        raise ValueError("container.xml 未声明 rootfile 的 full-path")
    return m.group(1)


def parse_opf(opf_text: str) -> dict[str, dict]:
    """OPF 文本 → {id: {href, media_type, properties}}（命名空间无关解析）。"""
    root = ET.fromstring(opf_text)
    items: dict[str, dict] = {}
    for el in root.iter():
        if el.tag.split("}")[-1] == "item":
            items[el.get("id") or ""] = {
                "href": el.get("href", ""),
                "media_type": el.get("media-type", ""),
                "properties": el.get("properties", "") or "",
            }
    return items


def extract_cover(zf: zipfile.ZipFile, opf_name: str | None = None) -> str | None:
    """四层回退找出封面图片在包内的路径；找不到返回 None。

    第 2 层需特别注意：<meta name="cover"> 的 content 既可能是 item 的 id，
    也可能是图片文件名（手制书常见）。ElementTree 对「无子节点的 meta 元素」
    用 `if meta:` 判断会误判为假，必须写 `is not None`。
    """
    if opf_name is None:
        opf_name = opf_path(zf)
    try:
        opf = zf.read(opf_name).decode("utf-8", "replace")
        items = parse_opf(opf)
    except (KeyError, ET.ParseError, FileNotFoundError, ValueError):
        return None
    base = posixpath.dirname(opf_name)

    # 第 1 层：properties="cover-image"
    for it in items.values():
        if "cover-image" in it["properties"].split():
            return _resolve(it["href"], base)

    # 第 2 层：<meta name="cover" content="...">
    m = re.search(r'<meta\b[^>]*name\s*=\s*["\']cover["\'][^>]*content\s*=\s*["\']([^"\']+)', opf)
    if not m:
        m = re.search(r'<meta\b[^>]*content\s*=\s*["\']([^"\']+)["\'][^>]*name\s*=\s*["\']cover["\']', opf)
    if m:
        name = m.group(1)
        it = items.get(name)
        if it is not None:                      # content 是 item id（最常见）
            return _resolve(it["href"], base)
        cand = _by_basename(items, name)        # content 直接是文件名（手制书）
        if cand is not None:
            return _resolve(cand["href"], base)

    # 第 3 层：<guide><reference type="cover"> 指向页面里的首图
    g = re.search(r'<guide\b.*?</guide>', opf, re.S)
    if g:
        ref = re.search(
            r'<reference\b[^>]*type\s*=\s*["\']cover["\'][^>]*href\s*=\s*["\']([^"\']+)',
            g.group(0))
        if ref:
            page = _resolve(ref.group(1).split("#")[0], base)
            try:
                html = zf.read(page).decode("utf-8", "replace")
                img = re.search(r'<(?:img[^>]+src|image[^>]+href)\s*=\s*["\']([^"\']+)', html, re.I)
                if img:
                    target = _resolve(img.group(1), posixpath.dirname(page))
                    if target in zf.namelist():
                        return target
            except KeyError:
                pass

    # 第 4 层：兜底——manifest 里 image/* 且 id 或 href 含 "cover"
    for it in items.values():
        if it["media_type"].startswith("image/") and "cover" in (it["id"] + it["href"]).lower():
            return _resolve(it["href"], base)
    return None


def _resolve(href: str, base: str) -> str:
    return posixpath.normpath(posixpath.join(base, href))


def _by_basename(items: dict, name: str) -> dict | None:
    name = name.rsplit("/", 1)[-1]
    for it in items.values():
        if posixpath.basename(it["href"]) == name:
            return it
    return None
