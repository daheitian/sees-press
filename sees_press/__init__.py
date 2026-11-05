# -*- coding: utf-8 -*-
"""见室印制所 SEES PRESS — 输入内容，自动产出排版精良的电子书。

一条书稿（IR）是唯一真相源，从它派生：
  EPUB 3（语义化 XHTML） / PDF（XeLaTeX + ctex） / 封面 PNG / 网页预览 / 质检报告
"""
__version__ = "1.0.0"
__all__ = ["build", "BuildOptions", "Book"]

from .pipeline import build, BuildOptions  # noqa: E402,F401
from .model import Book  # noqa: E402,F401
