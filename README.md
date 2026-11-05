# 见室印制所 · SEES PRESS

**输入内容，自动产出排版精良的电子书。** 一份书稿进去，EPUB 3、PDF、封面、网页预览、质检报告一起出来。

本机运行，不联网上传，不依赖任何第三方服务。核心只依赖 Python 标准库 + Pillow；检测到 TinyTeX 时会额外出 PDF，检测到 `publisher-typography` 脚本时会额外跑那套 23 项校验。

[![build](https://github.com/daheitian/sees-press/actions/workflows/ci.yml/badge.svg)](https://github.com/daheitian/sees-press/actions/workflows/ci.yml) [![license](https://img.shields.io/badge/license-MIT-30343A)](LICENSE)

> **先看成品**：[在线演示站](https://daheitian.github.io/sees-press/) · [在线翻看样例书](https://daheitian.github.io/sees-press/preview.html) · [七种版式试验台](https://daheitian.github.io/sees-press/try.html) · [下载 EPUB](https://daheitian.github.io/sees-press/assets/sample.epub) / [PDF](https://daheitian.github.io/sees-press/assets/sample.pdf)
>
> 演示站是静态的：展示真实产物、可翻页预览、可切换版式试排。真正生成 EPUB / PDF 的流水线在本机跑。

---

## 一、先跑起来

```bash
# 取源码
git clone https://github.com/daheitian/sees-press.git
cd sees-press
pip install -r requirements.txt     # 可选依赖，缺了不阻断

# 图形界面（推荐）：双击 启动.bat，或
python server.py --open

# 命令行
python -m sees_press.cli demo                      # 用示例文稿跑通全流程
python -m sees_press.cli build 稿件.md --title "书名" --author "作者" --style 疏朗留白
python -m sees_press.cli styles                    # 看七种版式
python -m sees_press.cli doctor                    # 环境自检
```

> 本机没有全局 `python` 时用绝对路径：
> `C:/Users/ga/.workbuddy/binaries/python/versions/3.13.12/python.exe`

界面在 `http://127.0.0.1:8777`。产物落在 `out/<任务号>/`。

---

## 二、它怎么把两套东西合到一起

### 来自 Standard Ebooks tools 的部分

`se` 要求 Python ≥ 3.14.4（本机 3.13），且只接受严格英文语料的 SE 源码目录，直接装不上也用不上中文稿。所以这里**内化它的能力，而不硬依赖它**：

| `se` 工具 | 本软件对应实现 |
|---|---|
| `create-draft` / `split-file` | `parse.py` 自动识别篇章、切块、生成 slug |
| `semanticate` | XHTML 打 `epub:type`：chapter / epigraph / footnote / noteref / titlepage / colophon / landmarks |
| `typogrify` | `typogrify.py` 智能引号、破折号、省略号、汉字邻接半角标点、重复标点收敛 |
| `clean` | 行尾空白、连续空行、不可见字符、中西文细空格（U+2009） |
| `find-unusual-characters` | 控制字符 / 零宽 / 私有区 / 乱码序列检测 |
| `find-mismatched-dashes` 等 | 连字符写法、省略号与破折号形式、引号风格一致性检查 |
| `build-toc` / `build-manifest` / `build-spine` / `build-ids` | `epub.py` 从 IR 自动生成 nav.xhtml、toc.ncx、manifest、spine，id 全派生不手写 |
| `lint` / `build --check` | `lint.py` 三道闸门 + `report.md` |
| `recompose-epub` | `preview.py` 把包重组成单文件离线 HTML（图片转 data URI、桌面/手机切换） |
| `build-images` | `cover.py` 程序化生成编辑印刷风封面 |

检测到 `se` 时会登记在环境自检里，但不会调用（中文语义规则不兼容）。

### 来自你原有 `publisher-typography` 流程的部分

| 既有资产 | 复用方式 |
|---|---|
| 七种版式实测参数（开本 / 边距 / 行距 / 专色） | `styles.py` 参数表，PDF 走 geometry+ctex，EPUB 走同一组数值映射的 CSS |
| `book.tex` / `body.tex` 双管线黄金法则 | `tex.py` 同时产出 `body.tex`（`\dukpart` / `\duksec` / `\bnote`）与整书 `book.tex`；**PDF 与 EPUB 互不覆盖** |
| `tex_to_epub.py` 的输入契约 | `body.tex` 完全兼容，可直接接回旧脚本重出 EPUB |
| `bake_cover.py` / `mono-color` | 封面 PNG 已有标题（书架取的是图本身）；`--cover` 可直接接管 mono-color 出的底图 |
| `validate_epub.py` 23 项校验 | `tools.py` 自动调用，输出并进 `report.md`；与本软件内置复核形成双保险 |
| `make_preview.py` | 先尝试调用，失败静默让位给内置预览 |
| 全部实测 LaTeX 坑 | 已固化进模板：`\duksec` 前缀避开内置 `\sec`、tikz overlay 满版封面、`\tableofcontents` 只调一次、`\rule` 前 `\noindent`、显式字号配 `\linespread{1.0}`、URL 危险字符先转义、缺字形兜底、封面/前置页页码连续、`figure[!tb]` 浮动体 |
| 编译后用 PyMuPDF 注书签 | `tools.inject_outline()` 在成品 PDF 上按章名定位，避开 `\pdfbookmark` 扰动分页与印刷页码/绝对页码错位 |

**最重要的一条继承**：正文源是唯一真相源，EPUB 绝不从 PDF 反推（分页产物转重排会碎）。本软件再进一层——以 IR 为唯一真相源，`body.tex`、XHTML、预览都从它派生。

---

## 三、流水线

```
输入（粘贴 / md / txt / docx / html / epub / URL / 目录）
  ↓  ingest.py        编码嗅探、OOXML/HTML 抽取、frontmatter
  ↓  typogrify.py     哨兵保护 Markdown 结构 → 标点净化
  ↓  parse.py         → Book(IR)：篇章 / 引文 / 列表 / 诗文 / 插图 / 脚注
  ├─→ tex.py          body.tex + book.tex ──→ XeLaTeX ×2 ──→ PDF（+ 书签注入）
  ├─→ epub.py         语义化 XHTML + nav/ncx/opf ──→ EPUB 3
  ├─→ cover.py        三段式半调墨稿封面（标题烤进 PNG）
  ├─→ preview.py      单文件离线 HTML 预览
  └─→ lint.py         闸门 A 书稿 / B 包结构与编译日志 → report.md
```

### 三道闸门

- **A · 书稿**：元数据、slug 冲突、脚注引用/定义配对、私有区残留、图片缺失、超长段落、异常字符、写法不一致
- **B · EPUB 包**：mimetype 包首且 STORED、OPF 必需元数据、spine↔manifest id 一致、nav 与 cover-image 属性、全 XML 合法、链接与图片死链、脚注配对、LaTeX/占位符残留
- **B · PDF**：Overfull >1pt、缺字形、`!` 编译错误

`report.md` 会同时附上 `validate_epub.py` 的原始输出。**错误标记会阻断「质检通过」**，但不会阻止其余产物生成——某条支线挂了不拖垮整本书。

---

## 四、输入语法

普通文本直接粘就行；软件会自动认「第一章」「一、」「卷一」「前言」「附录」这类裸标题。推荐用 Markdown：

```markdown
---
书名：论闲暇
作者：约瑟夫·皮珀
国别：德
版式：疏朗留白
---

# 论闲暇            ← 第一个 H1 当书名（已显式给书名时则当「篇」）

## 第一章　开端      ← 章，自动拆成编号 + 标题

::: epigraph
题词第一行
题词第二行
:::

正文，支持 **粗体**、*斜体*、`代码`、[链接](https://…)。
脚注这样写[^1]，中西文间距与标点由排版引擎处理。

> 一整段引文

- 列表项
- 列表项

### 小节标题

::: verse
诗文第一行
诗文第二行
:::

![图注](figs/diagram.png)

* * *                 ← 场景分隔

[^1]: 脚注内容，会变成可弹出脚注。
```

frontmatter 也认中文键：`书名 / 副题 / 作者 / 国别 / 译者 / 出版方 / 编者 / 版次 / ISBN / 简介 / 主题词 / 系列 / 版式 / 封面`。

---

## 五、版式

| 版式 | 开本 mm | 行距 | 专色 | 适合 |
|---|---|---|---|---|
| 疏朗留白 | 140×203 | 1.85 | 墨灰 | 思想随笔、译文单行本（默认） |
| 墨绿专色 | 145×210 | 1.70 | 墨绿 | 经典、学术、精装 |
| 文艺细线 | 145×210 | 1.75 | 赭橘 | 散文、游记、文学 |
| 窄边密排 | 125×185 | 1.50 | 纯黑 | 文库本、手册、长篇 |
| 撞色色块 | 148×195 | 1.70 | 橙 | 年轻、商业、图文书 |
| 素雅留白 | 130×185 | 1.90 | 灰绿 | 诗集、札记、小册 |
| 框线灰调 | 130×184 | 1.65 | 灰 | 古典、讲义、注释本 |

同一份书稿换个 `--style` 就能批量出多版样张对比。

---

## 六、命令行完整参数

```
python -m sees_press.cli build <输入> \
  --title "书名" --subtitle "副题" --author "作者" --region 德 \
  --translator "译者" --publisher "见室小册" --editor "编者" \
  --edition "2026 年 11 月第 1 版" --isbn 9787000000000 \
  --desc "简介" --subject "哲学,随笔" --series "见室小册" \
  --style 疏朗留白 --quote-style auto \
  --formats epub,pdf,cover,preview --cover my-cover.png \
  --out-dir ./out/mybook
```

- `--quote-style`：`auto`（中文「」/ 英文 “”）| `corner` | `curly` | `straight`（不改）
- `--no-cjk-spacing`：关掉 EPUB 的中西文细空格
- `--no-chapter-newpage`：PDF 不强制每章起新页
- `--no-legacy-validator`：不调用既有 23 项校验脚本
- `--no-body` / `--no-ir`：不输出 body.tex / book.json
- `--cover-ratio 3:4`：封面改 3:4（默认 2:3）

---

## 七、产出的文件

| 文件 | 说明 |
|---|---|
| `<书名>.epub` | 重排式 EPUB 3，双目录、可弹出脚注、语义化 XHTML，可上架微信读书 |
| `<书名>.pdf` | 整书：封面 + 书名页 + 版权页 + 目录 + 正文，含 PDF 书签 |
| `cover.png` | 三段式编辑印刷风封面（黑 + 暖白双色、半调网点、标题已烤入） |
| `preview.html` | 单文件离线预览，桌面 / 手机窄屏切换，可直接打印 |
| `body.tex` | 纯语义正文源，可直接喂给既有 `tex_to_epub.py` |
| `book.tex` | 完整 XeLaTeX 整书模板，可手工微调再编 |
| `book.json` | 书稿 IR 快照，便于二次加工 / 版本对比 |
| `report.md` | 质检报告（含外部校验器原始输出） |
| `validate_epub.txt` | 既有 23 项校验脚本的输出 |

---

## 八、已知边界

- **PDF 需要 XeLaTeX**（TinyTeX / TeX Live）。程序会先查 `xelatex` 是否在 PATH，再扫常见安装目录；装在别处时设环境变量 `SEES_PRESS_TEX_BIN` 指向其 `bin` 目录即可，`python -m sees_press.cli doctor` 可确认。没有 TeX 时 PDF 支线跳过并如实写进日志，EPUB / 封面 / 预览照常出。
- **翻译不在本软件职责内**。英文稿请先用 `en-zh-universal` 定稿再投进来，软件只做排版。
- **封面是程序化生成的**，不是 AI 生图。想要 copper-plate 半调人物肖像，用 `mono-color` 出底图 → `bake_cover.py` 烤字 → `--cover` 传进来即可。
- `se` 工具链未安装也不需要安装；其英文排版规则对中文不适用，本软件已做中文等价替换。
- 本机 Windows 的失效系统代理会让 `urllib` 的 HTTPS 握手失败，`ingest.fetch_url()` 已显式用 `ProxyHandler({})` 绕过；换机器若另有代理需求，改这一处。

---

## 九、目录

```
sees-press/
├─ server.py              本地 Web 服务（仅 127.0.0.1）
├─ 启动.bat                Windows 双击启动
├─ web/index.html          极简图形界面
├─ sees_press/
│  ├─ model.py             书稿 IR
│  ├─ styles.py            七种版式 + EPUB CSS
│  ├─ ingest.py            输入归一化
│  ├─ typogrify.py         排版净化 + 一致性/异常字符检测
│  ├─ inline.py            行内标记 token 化（递归，非正则）
│  ├─ parse.py             文本 → IR
│  ├─ lint.py              三道闸门 + 报告
│  ├─ tex.py               IR → body.tex / book.tex
│  ├─ epub.py              IR → 语义化 EPUB 3
│  ├─ cover.py             程序化封面
│  ├─ preview.py           recompose-epub 预览
│  ├─ tools.py             外部脚本 / TeX / se 适配
│  ├─ pipeline.py          编排
│  └─ cli.py               命令行
├─ examples/demo.md
├─ docs/                   GitHub Pages 演示站（成品预览 / 试验台 / 样例下载）
└─ out/                    产物（不入库）
```

---

## 十、在线演示站与持续集成

`docs/` 是 GitHub Pages 的站点根，从 `main` 分支的 `/docs` 目录发布：

- `index.html` 项目页：成品样张、七种版式、工作原理、快速开始、质检结果
- `preview.html` 由软件真实产出的单文件离线预览，图片已转 data URI，浏览器直接翻看
- `try.html` 纯前端版式试验台：粘贴文字、切换七种装帧参数、打印成 PDF（仅供挑版式，不生成 EPUB）
- `assets/` 样例书的封面、内页样张、EPUB 3 与 PDF

`.github/workflows/ci.yml` 在每次 push 时用 Python 3.13 跑通 `demo` 全流程，并校验 EPUB 的 mimetype 包首且 STORED、nav.xhtml / content.opf 存在、包内条目合法，最后把产物传成 Actions artifact。CI 上不装 TeX，PDF 支线按设计跳过。

## 十一、许可与致谢

MIT，见 [LICENSE](LICENSE)。

排版规则思路参考 Standard Ebooks tools（GPL-3.0）与 `publisher-typography` 的实测经验，本仓库未包含其任何源码；`se` 工具链不需要安装。`publisher-typography` 的脚本若存在于 `~/.workbuddy/skills/publisher-typography/scripts/`，会被自动复用形成双保险，缺失时全部降级到本包内置实现。


---

## 附：PDF → EPUB（重建，不是改后缀）

传入一份有文字层的 PDF，软件会把它当成**素材**而不是真相源：逐页抽取字词坐标 → 组行 →
剔除页眉页脚页码与封面/版权页/原目录 → 双栏按栏重排 → 合并中文断行 → 识别标题、引文、
列表、脚注与插图 → 输出干净的 Markdown 书稿，再由同一 IR 派生标准 EPUB 3。

```bash
# 只抽取并体检，不生成书
python -m sees_press.cli pdf2md 书籍.pdf --check

# 抽出可人工校对的书稿
python -m sees_press.cli pdf2md 书籍.pdf -o source.md

# 直接一步出 EPUB（可同时要 PDF / 封面 / 网页预览）
python -m sees_press.cli build 书籍.pdf --formats epub,cover,preview --style 疏朗留白
```

构建会把 `source.md`（抽取稿）、`pdf_extract.json`（诊断：页数 / 章节 / 剔除行数 /
字符保留率 / 警告）一并放进产物目录，便于人工校对后再投一次。扫描件（无文字层）会明确
报错，需先 OCR；复杂表格建议用 `--check` 先看保留率。

**在线版**：<https://daheitian.github.io/sees-press/pdf2epub.html> —— pdf.js 与打包器都随页面
一起放在 `docs/`，转换全程在浏览器内存里完成，PDF 不上传。在线版是精简管线（专注文本
结构），插图提取、PDF 大纲、脚注归位与 23 项包校验由本机软件提供。

> 约定不变：绝不把软件**自己刚生成的 PDF** 再倒回 EPUB（分页产物反推会丢语义）。
> 外部传入的 PDF 走的是「抽取 → 重建结构化书稿 → 再派生 EPUB」，PDF 只是素材入口。
