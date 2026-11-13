# 期刊全文下载器

一个简洁的 Windows 桌面工具：输入自己的 Elsevier API Key，选择 ScienceDirect 期刊和发表年份，下载有权限访问的 XML 全文，也可为已有 XML 补齐高清图片，批量合成期刊式 PDF。文库只保留 XML 和 PDF，辅助状态与图片缓存统一保存在本机应用数据中。

常用入口保留 **Remote Sensing of Environment（RSE）** 与 **ISPRS Journal of Photogrammetry and Remote Sensing**。点击「检索全部期刊」可按英文名称、关键词或 ISSN 搜索内置目录，也支持粘贴期刊地址。

**v1.9.0** 精简目录与检索流程：预览或开始时默认从 Elsevier 官方接口更新论文列表。关键词留空按 ISSN、发表年份获取整个期刊目录；有关键词使用 ScienceDirect 全文或题名检索。删除手动刷新、旧的本地题名筛选和重复的浏览器 / 范围设置。刚预览的完整列表可在 10 分钟内直接用于一次下载，避免重复请求。

重复分页自动合并并有限次补查；每页保存未完成进度，仍有缺口时明确提示，可先下载已确认匹配的论文。再次开始自动补查，已有完整 XML / PDF 校验后跳过。

## 下载与使用

在 [Releases](https://github.com/yaluncoco/journal-downloader/releases) 下载 `JournalDownloader-Setup.exe`，运行安装向导后从开始菜单启动。Windows 10/11 64 位，无需安装 Python。

1. 连接校园网或学校 VPN。
2. 输入自己的 API Key，选择预置期刊或粘贴期刊网址。
3. 选择起止年份、保存位置、本次数量（0 表示不限）和输出方式：仅 XML / XML + PDF / 仅 PDF。需要筛选时填写论文关键词，选择检索范围，先点「预览」核对题名再开始下载。
4. 已有 XML 时，点击「合成 PDF」，选择文库文件夹和 PDF 方式，开始生成。

XML 下载及默认 PDF 方式均不需要浏览器。自动方式优先保存通过全文校验的官方 PDF，否则通过内置引擎生成带图的单栏阅读版。只有在「合成 PDF」中明确选择「浏览器排版」时才需要 Edge 或 Chrome；该方式自动优先检测 Edge，也可手动选择 `msedge.exe` 或 `chrome.exe` 并运行「检测浏览器环境」。

右上角向上箭头用于软件更新。启动后在后台检查一次正式版本，有新版显示红点。点击「下载并安装」后下载并验证 SHA-256，再关闭当前窗口、打开安装向导；完成安装后可启动新版。安装日志保存在 `%LOCALAPPDATA%\JournalXMLDownloader\updates\版本号\setup.log`。临时网络失败最多重试两次，下载校验失败不会执行安装。

安装到当前用户目录，无需管理员权限，提供开始菜单、可选桌面快捷方式和卸载入口。安装、覆盖升级、卸载均保留论文和本机应用数据。程序仍在运行时安装器要求先关闭，不强制结束任务。旧便携 EXE 不会自动删除，也不要继续用它启动。

支持的地址示例：

```text
https://www.sciencedirect.com/journal/remote-sensing-of-environment
https://www.sciencedirect.com/journal/isprs-journal-of-photogrammetry-and-remote-sensing
https://www.sciencedirect.com/search?pub=Remote+Sensing+of+Environment&date=2024-2027
```

年份以界面选择为准。更多说明见 [使用说明](使用说明.md)。

## 功能

- 圆角界面、期刊快捷切换、年份选择与下载进度。
- 内置 5,211 条 ScienceDirect 期刊目录（2026-11-04 官方清单快照），支持名称关键词 / ISSN 搜索、分页及在刊 / 历史刊名筛选；搜索不请求 API。
- 目录加入官方学科筛选（包括农学×计算机、林学×计算机），显示 2025 中科院参考大类分区、排名、Top 与收录索引；缺失信息明确标注。
- 检索统一 Computer/Computers、and/&；正式刊名和顺序前缀优先，具体输入 `computer and electronic` 可将目标期刊排首位。
- 下载时可逐篇合成 PDF；默认保留 XML，明确选择「仅 PDF」才会在该篇 PDF 校验后移除 XML。合成失败保留 XML，下次复用并重试。
- 论文检索直接使用 ScienceDirect Search V2 PUT 接口；官网语法如 `(forest OR crop) AND "deep learning" NOT urban`。也可在「设置」中选任一 / 全部词组，由软件生成 OR / AND 表达式。按期刊精确词组查询并校验返回刊名，避免混入相似期刊。
- 点「预览」只取元数据，结果按 25 条分页；按 API 返回顺序下载，数量限制与续传沿用此顺序。输入时不联网，API 每页最多 100 条、默认间隔 3 秒；每次预览或开始自动更新；仅刚预览的完整列表可在 10 分钟内直接用于一次下载。缓存供本地统计，不代替下次更新。超过 6,000 条要求缩小范围，不静默截断。
- 关键词留空时通过 Elsevier Article Metadata API 按 ISSN 和发表年份获取期刊目录，最新发表优先。列表和全文均需相应授权；401/403 不解释为零结果，不使用旧列表掩盖失败。旧本地题名设置迁移到官方题名检索，关键词保留。
- 切换目录时立即清除旧 PDF 状态，按所选目录与年份重新检查 PDF 实际内容、哈希和 DOI。
- 打开软件、切换保存位置或年份时，后台读取已有 XML 并显示完整数量；选择具体文库、`fulltext_xml` 或年份文件夹可识别原文库。点击开始后继续下载缺少的论文。
- 按 DOI 识别改过文件名的完整 XML；下载前重新检查完整性，避免重复下载。
- 暂停、继续、安全停止和再次启动后的续传。
- 通过 XML 解析、DOI、正文与 SHA-256 校验后才跳过已有文件。
- 可设置本次成功下载篇数；已有完整文件、失败和重试不占名额，达到数量自动结束，再次开始可继续下一批。
- 临时网络错误自动重试，遵守服务器限流与配额等待时间。
- 默认直连 Elsevier，避免系统 HTTP 代理影响机构权限识别。
- API Key 默认使用 Windows DPAPI 加密记忆，下次自动填入；取消「本机加密记住 Key」会清除已记忆的 Key。普通设置、日志和文库中不保存明文 Key。
- 不再生成 TXT；旧版日志、校验记录、图片缓存和 TXT 会迁移到本机应用数据，已有 XML 与 PDF 保持原位置。
- 同一期刊按年份归档，多个期刊按 ISSN 分开保存。
- 独立的 PDF 合成入口：读取现有结构化 XML，补齐高清图片，保留图题、表格、MathML 公式及参考文献。
- 左下角固定显示 PDF 进度，全部成功后显示绿色「PDF 已生成完成」；部分失败、未找到 XML 和停止状态分别提示。
- 内置排版为单栏阅读版；可手动选择浏览器双栏 / 单栏排版。内置字体、MathML 公式绘制和图片处理随安装包分发；遇到不支持的公式、字体或过宽表格时保留 XML 并提示，不静默丢弃内容。
- 彩色数字、文字等行内图片按文字字号排版，仍使用高清图片；表头与首行保持在一起，末尾比较行尽量同页。
- 图片缓存解码与 SHA-256 校验；PDF 校验 XML 哈希、文件哈希、DOI、全部页流及页数。自动模式可复用既有完整阅读版；手动选择排版方式时同时核对版式与排版版本。
- 单篇缺图、XML 损坏或不支持的内容记录待处理，继续其他论文；再次合成可重试。
- 向上箭头更新入口、新版红点提示、安装包 SHA-256 校验、网络重试与安装日志；新版启动后才确认更新完成。
- 适配屏幕工作区和 100%–200% DPI 缩放：宽屏双栏、窄屏上下排列，底部操作保持可见，长刊名自动换行。

## 范围说明

论文目录与关键词结果来自 Elsevier 官方 API，可能包含勘误、编委会等记录。软件校验返回期刊、发表年份和 DOI，缺少有效 DOI 的条目单独计数。未来年份只能获取目前已登记、已发布的条目；接口索引与网页更新时间可能不同。Crossref 仅用于识别内置目录中没有的自定义期刊，不再用于获取论文列表。

全文由 Elsevier Article Retrieval API 提供，插图由 Object Retrieval API 提供。API Key、机构订阅及网络出口决定实际可访问范围。本程序不绕过访问控制。

PDF 默认优先下载并校验官方原版；官方全文不可用时，依据 XML 和图片重新排版为带来源说明的阅读版，字体、分页和图表位置可能与原版不同。rawtext / 仅摘要 XML 不会被标为完整图文 PDF，需保留为待处理；补充附件不嵌入 PDF。

IEEE 接口目前处于验证阶段，尚未启用下载入口。测试账号仍待审批，首次请求返回 `403 Developer Inactive`；格式与权限说明见 [IEEE 接口核对](IEEE接口说明.md)。

## 从源码运行

建议 Python 3.12，Windows 环境：

```powershell
python -m venv .build-env
.\.build-env\Scripts\python.exe -m pip install -r requirements-build.txt
.\.build-env\Scripts\python.exe app.py
```

## 测试与构建

```powershell
.\.build-env\Scripts\python.exe -m unittest -v test_engine test_controller test_pdf test_updater test_storage test_catalog test_inventory test_pipeline test_layout
.\build.ps1 -ISCC "C:\Program Files\Inno Setup 7\ISCC.exe"
```

安装器编译需安装官方 Inno Setup 7。产物为 `release/v1.9.0/JournalDownloader-Setup.exe`；应用目录为 `release/v1.9.0/JournalDownloader/`。XML 下载引擎使用 Python 标准库；界面使用 CustomTkinter，图片校验使用 Pillow，PDF 校验使用 pypdf，使用 PyInstaller 打包。内置排版的依赖及字体随安装包分发；可选的浏览器不随程序分发。

打包后离线自检：

```powershell
.\release\v1.9.0\JournalDownloader\JournalDownloader.exe --self-test "$env:TEMP\journal-downloader-test.json"
```

加入 `--online` 可额外验证两个期刊的在线元数据查询，不需要真实 API Key。
加入 `--pdf` 可额外验证内置 PDF、Unicode 字符、MathML 公式、表格、完整文件跳过及损坏 PDF 重建，并检查未启动子进程；不需要安装浏览器。

无网络批量合成（要求图片已缓存；API Key 不通过命令行传递）：

```powershell
.\release\v1.9.0\JournalDownloader\JournalDownloader.exe --pdf-export "D:\文库\fulltext_xml" "D:\文库\fulltext_pdf"
```

结果保存在本机应用数据中的 `offline_export_result.json`，输出目录只放 PDF；默认使用内置单栏排版。首次获取图片请使用界面输入 Key。

需要浏览器排版时，离线合成可追加 `--browser-render` 自动检测 Edge / Chrome，或 `--pdf-browser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"` 只调用指定浏览器；路径应替换成目标电脑的实际位置。浏览器模式可加 `--single-column` 选择单栏，命令行不读取界面的已保存浏览器设置。

## 代码结构

| 文件 | 用途 |
| --- | --- |
| `app.py`、`app_runtime.py` | 程序入口、安装器互斥与独立启动环境 |
| `installer.iss`、`build_installer.py` | Windows 安装/卸载与可复现构建 |
| `window_layout.py`、`test_layout.py` | DPI/工作区适配、响应布局与尺寸回归测试 |
| `ui.py` | 界面布局与主题 |
| `controller.py` | 工作线程、进度事件与设置 |
| `engine.py` | 期刊识别、元数据索引、下载与重试 |
| `xml_utils.py` | XML 校验与原子保存 |
| `library_store.py` | 辅助数据迁移、本机状态与完整性记录 |
| `credential_store.py` | Windows DPAPI 加密 Key 记忆与清除 |
| `library_inventory.py` | 本地 XML 扫描、文库识别与按 DOI 续传 |
| `library_preview.py`、`pdf_inventory.py` | 后台目录状态预览、PDF 完整性扫描 |
| `article_filter.py`、`download_output.py` | 词组输入、逐篇输出与 PDF 完成后清理 |
| `article_search.py`、`journal_articles.py`、`search_paging.py`、`article_search_ui.py` | 官方目录 / 检索、自动更新、重复分页恢复和预览 |
| `journal_catalog.py`、`catalog_ui.py`、`journals.json` | 离线期刊检索与目录数据 |
| `journal_metadata.py`、`journal_metadata.json`、`build_metadata.py` | 官方学科、2025 分区参考与可复现数据转换 |
| `build_catalog.py` | 从官方 XLSX 清单构建目录快照 |
| `pdf_assets.py` | 图片对象获取、重试与缓存校验 |
| `pdf_document.py` | XML、CALS 表格、MathML 到安全排版 HTML |
| `pdf_export.py` | PDF 批量生成、排版选择和断点校验 |
| `pdf_native.py` / `pdf_official.py` | 内置单栏阅读版与官方全文 PDF 校验 |
| `browser_detection.py`、`browser_environment.py`、`pdf_browser.py` | Edge/Chrome 识别与手动路径、独立打印、启动状态与离线诊断 |
| `updater.py`、`update_ui.py` | 检查版本、校验安装包、独立进程启动安装向导及完成记录 |
| `test_engine.py`、`test_controller.py`、`test_pdf.py`、`test_updater.py`、`test_storage.py` | 引擎、界面、PDF、更新、数据迁移和 Key 加密测试 |
| `smoke_test.py` | 打包程序自检 |

仓库不包含 API Key、用户配置或下载的论文全文。运行产生的文库、日志和构建产物已加入忽略规则。

辅助数据在 `%LOCALAPPDATA%\JournalXMLDownloader\library-data\`。迁移只处理程序已知的辅助文件，保留未知文件；不同内容的同名旧记录会归档，避免覆盖。搬动文库或更换电脑后，本机完整性记录和图片缓存不会自动跟随，可能需要重新验证 XML、重新合成 PDF 或下载图片。

目录来自 [Elsevier ScienceDirect 官方期刊清单](https://www.elsevier.com/products/sciencedirect/journals) 的在刊、即将出版和出版变更表，按 ISSN 合并，保留别名并排除表中一个图书丛书条目。它是随软件发布的快照，包含更名、停刊和已转出刊名，不能据此保证某刊当前可通过 API 下载。目录搜索不访问网络，也不消耗 API 配额；论文列表和全文下载仍按原有流程请求。开发者可运行 `python build_catalog.py --source-dir .publish/catalog --download` 更新快照，打包时必须包含 `journals.json`。

## 图标

EXE、窗口和界面使用用户指定的 [Alpine 图标](https://iconic-api.onrender.com/dark/alpine)。`app.svg` 保留原始矢量图，`app.png` 用于界面，`app.ico` 包含 16–256 像素的多种尺寸，供 Windows 使用。

修改图标时，先将 SVG 渲染为至少 256 × 256 的透明 PNG，再运行 `python make_icon.py` 生成 ICO。仓库已包含这些文件，正常打包无需额外安装 SVG 渲染工具。

### PDF 与校园网权限

检索与 XML 下载通过官方 API 完成，不读取浏览器 Cookie。校园网 / VPN 出口必须由 Elsevier 识别，网页登录的会话权限不会自动成为 API 权限。API Key、机构授权、代理出口和配额仍决定可下载内容。

在「合成 PDF」里选择方式（也用于下载时生成）：
- **自动：官方优先 / 内置单栏**：默认；先获取 XML 作为正文与页数依据，再请求 `view=FULL` 的官方表示（请求头 `Accept: application/pdf`）。校验 DOI、文件结构及 `xocs:web-pdf-page-count`，完整时保存原版，不完整或未授权时用 XML 和图片生成阅读版。同一次会话遇到订阅 PDF 预览后不反复请求后续订阅 PDF，开放获取内容仍可尝试。
- **内置单栏**：跳过官方 PDF 探测，直接用 XML 和图片排版，不启动浏览器。正文、表格为可检索文本，公式以高清图像嵌入。
- **浏览器排版**：明确选择后才检测并调用 Edge / Chrome，可选择双栏或单栏。

官方原版和 XML 阅读版是两种不同产物，阅读版的分页与期刊版式不相同。仅 PDF 模式仍会暂存 XML，只有最终 PDF 通过校验且校验记录写入成功后才删除该篇 XML。任何失败保留源文件。

接口依据：[Article Retrieval](https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl)、[API authentication](https://dev.elsevier.com/tecdoc_api_authentication.html)。本机订阅样本 XML 记录 17 页，官方 PDF 却仅返回 1 页，因此不能用 HTTP 200 或 `%PDF` 文件头代替全文校验。
