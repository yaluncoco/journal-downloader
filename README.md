# 期刊全文下载器

一个简洁的 Windows 桌面工具：输入自己的 Elsevier API Key，选择 ScienceDirect 期刊和发表年份，下载有权限访问的 XML 全文，也可为已有 XML 补齐高清图片，批量合成期刊式 PDF。文库只保留 XML 和 PDF，辅助状态与图片缓存统一保存在本机应用数据中。

常用入口保留 **Remote Sensing of Environment（RSE）** 与 **ISPRS Journal of Photogrammetry and Remote Sensing**。点击「检索全部期刊」可按英文名称、关键词或 ISSN 搜索内置目录，也支持粘贴期刊地址。

## 下载与使用

在 [Releases](https://github.com/yaluncoco/journal-downloader/releases) 下载 `JournalDownloader-Windows-x64.zip`，解压后双击 `JournalDownloader.exe`。Windows 10/11 64 位，无需安装 Python。

1. 连接校园网或学校 VPN。
2. 输入自己的 API Key，选择预置期刊或粘贴期刊网址。
3. 选择起止年份、保存位置和本次下载数量（0 表示不限），点击「开始下载」。
4. 已有 XML 时，点击「合成 PDF」，选择文库文件夹和双栏 / 单栏版式，开始合成。

PDF 合成需要电脑已安装 Microsoft Edge 或 Google Chrome。程序使用独立的后台浏览器进程，不需要打开浏览器窗口或安装 Python。

右上角向上箭头用于软件更新。启动后会在后台检查一次 GitHub 正式版本，有新版时显示红点；检查失败不影响下载。点击箭头可重新检查并选择「下载并更新」。只有确认后才会下载新版，下载校验和新版自检通过后自动替换并重启，文库和设置保持原位置。

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
- 期刊双栏 / 舒适单栏排版，宽图表跨栏、长表格跨页、较长公式自动调整宽度。
- 彩色数字、文字等行内图片按文字字号排版，仍使用高清图片；表头与首行保持在一起，末尾比较行尽量同页。
- 图片缓存解码与 SHA-256 校验；PDF 按 XML 哈希、版式、排版版本、文件哈希与可读取性校验后跳过。
- 单篇缺图、XML 损坏或不支持的内容记录待处理，继续其他论文；再次合成可重试。
- 紧凑的向上箭头更新入口、新版红点提示，下载 SHA-256 校验、新版启动自检、原程序备份及替换失败恢复。

## 范围说明

论文列表来自 Crossref 公共元数据，是候选列表，不保证与 ScienceDirect 的全部卷期条目一一对应，也可能包含勘误、编委会等记录。未来年份只能获取已经登记、已经发布且有权限的内容。

全文由 Elsevier Article Retrieval API 提供，插图由 Object Retrieval API 提供。API Key、机构订阅及网络出口决定实际可访问范围。本程序不绕过访问控制。

PDF 是依据 XML 和图片重新排版的阅读版，标有来源说明；不是出版社官方 PDF，不能保证相同字体、分页或图表位置。rawtext / 仅摘要 XML 不会被标为完整图文 PDF，需保留为待处理；补充附件不嵌入 PDF。

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
.\.build-env\Scripts\python.exe -m unittest -v test_engine test_controller test_pdf test_updater test_storage test_catalog test_inventory
.\build.ps1
```

EXE 输出到 `release/JournalDownloader.exe`。XML 下载引擎使用 Python 标准库；界面使用 CustomTkinter，图片校验使用 Pillow，PDF 校验使用 pypdf，使用 PyInstaller 打包。浏览器不随程序分发。

打包后离线自检：

```powershell
.\release\JournalDownloader.exe --self-test "$env:TEMP\journal-downloader-test.json"
```

加入 `--online` 可额外验证两个期刊的在线元数据查询，不需要真实 API Key。
加入 `--pdf` 可额外验证 PDF 生成、完整文件跳过及损坏 PDF 重建，需要已安装 Edge/Chrome。

无网络批量合成（要求图片已缓存；API Key 不通过命令行传递）：

```powershell
.\release\JournalDownloader.exe --pdf-export "D:\文库\fulltext_xml" "D:\文库\fulltext_pdf"
```

结果保存在本机应用数据中的 `offline_export_result.json`，输出目录只放 PDF；可加 `--single-column` 改为单栏。首次获取图片请使用界面输入 Key。

## 代码结构

| 文件 | 用途 |
| --- | --- |
| `app.py` | 程序入口 |
| `ui.py` | 界面布局与主题 |
| `controller.py` | 工作线程、进度事件与设置 |
| `engine.py` | 期刊识别、元数据索引、下载与重试 |
| `xml_utils.py` | XML 校验与原子保存 |
| `library_store.py` | 辅助数据迁移、本机状态与完整性记录 |
| `credential_store.py` | Windows DPAPI 加密 Key 记忆与清除 |
| `library_inventory.py` | 本地 XML 扫描、文库识别与按 DOI 续传 |
| `journal_catalog.py`、`catalog_ui.py`、`journals.json` | 离线期刊检索与目录数据 |
| `build_catalog.py` | 从官方 XLSX 清单构建目录快照 |
| `pdf_assets.py` | 图片对象获取、重试与缓存校验 |
| `pdf_document.py` | XML、CALS 表格、MathML 到安全排版 HTML |
| `pdf_export.py` | PDF 批量合成、后台浏览器和断点校验 |
| `updater.py`、`update_ui.py` | 手动检查版本、校验下载、替换及失败恢复 |
| `test_engine.py`、`test_controller.py`、`test_pdf.py`、`test_updater.py`、`test_storage.py` | 引擎、界面、PDF、更新、数据迁移和 Key 加密测试 |
| `smoke_test.py` | 打包程序自检 |

仓库不包含 API Key、用户配置或下载的论文全文。运行产生的文库、日志和构建产物已加入忽略规则。

辅助数据在 `%LOCALAPPDATA%\JournalXMLDownloader\library-data\`。迁移只处理程序已知的辅助文件，保留未知文件；不同内容的同名旧记录会归档，避免覆盖。搬动文库或更换电脑后，本机完整性记录和图片缓存不会自动跟随，可能需要重新验证 XML、重新合成 PDF 或下载图片。

目录来自 [Elsevier ScienceDirect 官方期刊清单](https://www.elsevier.com/products/sciencedirect/journals) 的在刊、即将出版和出版变更表，按 ISSN 合并，保留别名并排除表中一个图书丛书条目。它是随软件发布的快照，包含更名、停刊和已转出刊名，不能据此保证某刊当前可通过 API 下载。目录搜索不访问网络，也不消耗 API 配额；论文列表和全文下载仍按原有流程请求。开发者可运行 `python build_catalog.py --source-dir .publish/catalog --download` 更新快照，打包时必须包含 `journals.json`。

## 图标

EXE、窗口和界面使用用户指定的 [Alpine 图标](https://iconic-api.onrender.com/dark/alpine)。`app.svg` 保留原始矢量图，`app.png` 用于界面，`app.ico` 包含 16–256 像素的多种尺寸，供 Windows 使用。

修改图标时，先将 SVG 渲染为至少 256 × 256 的透明 PNG，再运行 `python make_icon.py` 生成 ICO。仓库已包含这些文件，正常打包无需额外安装 SVG 渲染工具。
