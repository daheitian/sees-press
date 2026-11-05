# -*- coding: utf-8 -*-
r"""外部工具适配层：接回 publisher-typography 已固化的脚本与本机 TeX。

设计成「有则复用、无则降级」：软件本体只依赖 Python 标准库 + Pillow，
即使把 sees-press 单独拷到别的机器，EPUB / 封面 / 预览 / 内置校验照常工作；
检测到既有 skill 脚本时，额外跑一遍那套 23 项校验与浏览器预览，形成双保险。

关于 Standard Ebooks tools：其 `se` 要求 Python >= 3.14.4（本机 3.13），
且只接受严格的 SE 源码目录结构（英文语料规则）。因此这里不硬依赖 `se`，
而是把 typogrify / clean / lint / semanticate / build-toc / build-manifest /
build-spine / recompose-epub 的能力内化进本包；检测到 `se` 时仅登记可用性。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PY = sys.executable
HOME = Path.home()
SKILL_DIR = HOME / ".workbuddy" / "skills" / "publisher-typography" / "scripts"


def _tex_dirs() -> list[Path]:
    r"""TeX 可执行文件目录。

    优先读环境变量 SEES_PRESS_TEX_BIN，再扫常见安装位置，最后才交给 PATH。
    不把任何一台机器的具体盘符写死进代码：换机器只需设一个环境变量。
    """
    dirs: list[Path] = []
    override = os.environ.get("SEES_PRESS_TEX_BIN", "").strip()
    if override:
        dirs.append(Path(override))
    if os.name == "nt":
        for drive in ("C", "D", "E"):
            dirs.append(Path(f"{drive}:/oid_work/tinytex/TinyTeX/bin/windows"))
        dirs += [
            HOME / "WorkBuddy" / "tinytex" / "TinyTeX" / "bin" / "windows",
            HOME / "AppData" / "Local" / "Programs" / "TinyTeX" / "bin" / "windows",
        ]
    else:
        dirs += [
            Path("/usr/local/texlive/2025/bin/x86_64-linux"),
            Path("/usr/local/texlive/bin/x86_64-linux"),
            HOME / ".TinyTeX" / "bin" / "x86_64-linux",
        ]
    return dirs


TINYTEX_CANDIDATES = _tex_dirs()


def skill_script(name: str) -> Path | None:
    p = SKILL_DIR / name
    return p if p.exists() else None


def run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 300) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                           capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out
    except FileNotFoundError:
        return 127, f"命令不存在：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"超时：{' '.join(cmd[:3])}"


def validate_epub_legacy(epub: str | Path) -> str | None:
    """调用既有 validate_epub.py（23 项），返回原始输出。"""
    s = skill_script("validate_epub.py")
    if not s:
        return None
    code, out = run([PY, str(s), str(epub)], timeout=60)
    return f"[validate_epub.py · exit {code}]\n{out.strip()}"


def legacy_preview(epub: str | Path, out_html: str | Path) -> bool:
    """既有 make_preview.py 假定自己的包结构，失败就静默让位给内置预览。"""
    s = skill_script("make_preview.py")
    if not s:
        return False
    code, _ = run([PY, str(s), str(epub), str(out_html)], timeout=60)
    return code == 0 and Path(out_html).exists()


def legacy_bake_cover(base_png: str | Path, out_png: str | Path, meta) -> bool:
    """可选：用既有 bake_cover.py 烤标题（短标题场景）。"""
    s = skill_script("bake_cover.py")
    if not s:
        return False
    cmd = [PY, str(s), "--in", str(base_png), "--out", str(out_png),
           "--title", meta.title, "--title-color", "#30343A"]
    if meta.subtitle:
        cmd += ["--subtitle", meta.subtitle]
    if meta.author:
        cmd += ["--author", meta.author_line() or meta.author]
    code, _ = run(cmd, timeout=90)
    return code == 0 and Path(out_png).exists()


def se_available() -> bool:
    return shutil.which("se") is not None


def find_xelatex() -> str | None:
    p = shutil.which("xelatex")
    if p:
        return p
    for d in TINYTEX_CANDIDATES:
        exe = d / ("xelatex.exe" if os.name == "nt" else "xelatex")
        if exe.exists():
            return str(exe)
        # 把 TeX bin 挂进 PATH 后让 shutil 再找一次
    for d in TINYTEX_CANDIDATES:
        if d.exists():
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            p = shutil.which("xelatex")
            if p:
                return p
    return None


def compile_pdf(work: str | Path, tex_name: str = "book.tex",
                passes: int = 2) -> tuple[Path | None, str]:
    """两遍 xelatex 填充目录与交叉引用，返回 (pdf, 合并日志)。"""
    work = Path(work)
    exe = find_xelatex()
    if not exe:
        return None, "未找到 xelatex（TinyTeX）。PDF 管线跳过；EPUB / 封面 / 预览照常产出。"
    logs = []
    for i in range(passes):
        code, out = run([exe, "-interaction=nonstopmode", "-halt-on-error",
                         tex_name], cwd=work, timeout=300)
        # 只保留编译错误行；详细诊断最终只看 .log，避免重复计数
        if code != 0:
            logs.append(f"--- pass {i + 1} (exit {code}) ---\n{out[-4000:]}")
    logfile = work / (Path(tex_name).stem + ".log")
    if logfile.exists():
        logs.append(logfile.read_text(encoding="utf-8", errors="replace")[-30000:])
    pdf = work / (Path(tex_name).stem + ".pdf")
    return (pdf if pdf.exists() else None), "\n".join(logs)


def inject_outline(pdf: str | Path, book) -> str:
    r"""编译后用 PyMuPDF 注入目录树 —— 比在 LaTeX 侧 \pdfbookmark 可靠。

    用户流程实测：\pdfbookmark 会扰动分页且大量静默失效；
    且 \label 记的是印刷页码、set_toc 要 PDF 绝对页，前置页重编号会全错位。
    这里改为直接在成品 PDF 上按「正文起始页之后首次出现章名」定位。
    """
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return "未安装 PyMuPDF，跳过 PDF 书签注入"
    try:
        doc = pymupdf.open(str(pdf))
        total = len(doc)
        # 前 4 页：封面 / 书名页 / 版权页 / 目录，从其后开始找正文
        start = min(4, total - 1)
        toc = [[1, "封面", 1], [1, "目录", min(4, total)]]
        for c in book.chapters:
            key = "".join(c.display_title.split())
            found = None
            for pno in range(start, total):
                txt = "".join(doc[pno].get_text().split())
                if key and key in txt and len(txt) > 60:
                    found = pno + 1
                    break
            if found:
                toc.append([1 if c.level == 1 else 2, c.toc_title, found])
        if len(toc) > 2:
            doc.set_toc(toc)
            doc.saveIncr()
        doc.close()
        return f"PDF 书签注入 {len(toc) - 2} 条章目录"
    except Exception as e:
        return f"PDF 书签注入失败（不影响成品）：{e}"


def environment_report() -> dict:
    return {
        "python": sys.version.split()[0],
        "xelatex": find_xelatex() or "未安装（PDF 管线不可用）",
        "tex_bin_override": os.environ.get("SEES_PRESS_TEX_BIN") or "未设置（自动探测）",
        "pillow": _module("PIL"),
        "pymupdf": _module("pymupdf") or _module("fitz"),
        "publisher_typography_scripts": str(SKILL_DIR) if SKILL_DIR.is_dir() else "未找到（降级为内置实现）",
        "validate_epub_py": "可用" if skill_script("validate_epub.py") else "不可用",
        "make_preview_py": "可用" if skill_script("make_preview.py") else "不可用",
        "bake_cover_py": "可用" if skill_script("bake_cover.py") else "不可用",
        "standard_ebooks_se": "已安装" if se_available() else
                              "未安装（要求 Python ≥ 3.14.4；其规则已内化进本软件）",
    }


def _module(name: str) -> str:
    try:
        m = __import__(name)
        return getattr(m, "__version__", "可用")
    except ImportError:
        return ""
