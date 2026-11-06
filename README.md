# 期刊全文下载器

一个简洁的 Windows 桌面工具：输入自己的 Elsevier API Key，选择 ScienceDirect 期刊和发表年份，下载有权限访问的 XML 全文，也可为已有 XML 补齐高清图片，批量合成期刊式 PDF。文库只保留 XML 和 PDF，辅助状态与图片缓存统一保存在本机应用数据中。

常用入口保留 **Remote Sensing of Environment（RSE）** 与 **ISPRS Journal of Photogrammetry and Remote Sensing**。点击「检索全部期刊」可按英文名称、关键词或 ISSN 搜索内置目录，也支持粘贴期刊地址。

**v1.6.3** 优化启动、连续缩放与大文库响应：控件改用无需私有字体的 Canvas 绘制，避免滚动条和下拉框重绘时反复刷新整窗；准备好首次布局后再显示。目录预览只保留一个扫描线程，未变化文件在本次运行中复用校验结果，下载续传仍重新校验。期刊结果卡片复用并分批更新，运行日志批量写入，界面事件分时处理。

**v1.6.2** 修复深层临时目录下 PDF 检测失败却无法导出报告的问题：自动选择较短、可写的工作目录，诊断补充浏览器版本、实际工作路径和失败退出码；测试页通过不会覆盖论文合成失败的结果。

**v1.6.1** 修复跨电脑 PDF 启动兼容与旧文库续传：打印前离线检测 Edge/Chrome，自动尝试可用浏览器；故障时保留退出码及诊断记录。下载与合成同时识别文库年份目录和 `fulltext_xml`，支持勘误的 `simple-article` 结构。在「合成 PDF」中点击「检测 PDF 环境」可导出诊断文件，无需 Key 或联网。

**v1.6.0** 改为 Windows 安装版，更新通过安装向导完成。窗口按可用屏幕与 DPI 调整大小，窄窗口改为上下排列，检索和弹窗随窗口换行。首次从旧便携版迁移，请关闭旧程序并手动运行安装包一次；后续从开始菜单启动。

v1.5.0 新增逐篇 XML→PDF、仅 PDF 输出、下载前题名词组筛选、当前目录 PDF 完整性统计，以及官方学科与 2025 中科院参考分区筛选。数据来源及 GitHub 示例参考见 [DATA_SOURCES.md](DATA_SOURCES.md)。

## 下载与使用

在 [Releases](https://github.com/yaluncoco/journal-downloader/releases) 下载 `JournalDownloader-Setup.exe`，运行安装向导后从开始菜单启动。Windows 10/11 64 位，无需安装 Python。

1. 连接校园网或学校 VPN。
2. 输入自己的 API Key，选择预置期刊或粘贴期刊网址。
3. 选择起止年份、保存位置、本次数量（0 表示不限）和输出方式：仅 XML / XML + PDF / 仅 PDF。可填写题名关键词后开始下载。
4. 已有 XML 时，点击「合成 PDF」，选择文库文件夹和双栏 / 单栏版式，开始合成。

PDF 合成需要电脑已安装 Microsoft Edge 或 Google Chrome。程序使用独立的后台浏览器进程，不需要打开浏览器窗口或安装 Python。

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
- 下载前按论文题名匹配任一 / 全部词组，完整候选索引保持不变；只匹配题名，不声称检索全文或作者关键词。
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
- 期刊双栏 / 舒适单栏排版，宽图表跨栏、长表格跨页、较长公式自动调整宽度。
- 彩色数字、文字等行内图片按文字字号排版，仍使用高清图片；表头与首行保持在一起，末尾比较行尽量同页。
- 图片缓存解码与 SHA-256 校验；PDF 按 XML 哈希、版式、排版版本、文件哈希与可读取性校验后跳过。
- 单篇缺图、XML 损坏或不支持的内容记录待处理，继续其他论文；再次合成可重试。
- 向上箭头更新入口、新版红点提示、安装包 SHA-256 校验、网络重试与安装日志；新版启动后才确认更新完成。
- 适配屏幕工作区和 100%–200% DPI 缩放：宽屏双栏、窄屏上下排列，底部操作保持可见，长刊名自动换行。

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
.\.build-env\Scripts\python.exe -m unittest -v test_engine test_controller test_pdf test_updater test_storage test_catalog test_inventory test_pipeline test_layout
.\build.ps1 -ISCC "C:\Program Files\Inno Setup 7\ISCC.exe"
```

安装器编译需安装官方 Inno Setup 7。产物为 `release/v1.6.0/JournalDownloader-Setup.exe`；应用目录为 `release/v1.6.0/JournalDownloader/`。XML 下载引擎使用 Python 标准库；界面使用 CustomTkinter，图片校验使用 Pillow，PDF 校验使用 pypdf，使用 PyInstaller 打包。浏览器不随程序分发。

打包后离线自检：

```powershell
.\release\v1.6.0\JournalDownloader\JournalDownloader.exe --self-test "$env:TEMP\journal-downloader-test.json"
```

加入 `--online` 可额外验证两个期刊的在线元数据查询，不需要真实 API Key。
加入 `--pdf` 可额外验证 PDF 生成、完整文件跳过及损坏 PDF 重建，需要已安装 Edge/Chrome。

无网络批量合成（要求图片已缓存；API Key 不通过命令行传递）：

```powershell
.\release\v1.6.0\JournalDownloader\JournalDownloader.exe --pdf-export "D:\文库\fulltext_xml" "D:\文库\fulltext_pdf"
```

结果保存在本机应用数据中的 `offline_export_result.json`，输出目录只放 PDF；可加 `--single-column` 改为单栏。首次获取图片请使用界面输入 Key。

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
| `article_filter.py`、`download_output.py` | 题名筛选、逐篇输出与 PDF 完成后清理 |
| `journal_catalog.py`、`catalog_ui.py`、`journals.json` | 离线期刊检索与目录数据 |
| `journal_metadata.py`、`journal_metadata.json`、`build_metadata.py` | 官方学科、2025 分区参考与可复现数据转换 |
| `build_catalog.py` | 从官方 XLSX 清单构建目录快照 |
| `pdf_assets.py` | 图片对象获取、重试与缓存校验 |
| `pdf_document.py` | XML、CALS 表格、MathML 到安全排版 HTML |
| `pdf_export.py` | PDF 批量合成、后台浏览器和断点校验 |
| `updater.py`、`update_ui.py` | 检查版本、校验安装包、独立进程启动安装向导及完成记录 |
| `test_engine.py`、`test_controller.py`、`test_pdf.py`、`test_updater.py`、`test_storage.py` | 引擎、界面、PDF、更新、数据迁移和 Key 加密测试 |
| `smoke_test.py` | 打包程序自检 |

仓库不包含 API Key、用户配置或下载的论文全文。运行产生的文库、日志和构建产物已加入忽略规则。

辅助数据在 `%LOCALAPPDATA%\JournalXMLDownloader\library-data\`。迁移只处理程序已知的辅助文件，保留未知文件；不同内容的同名旧记录会归档，避免覆盖。搬动文库或更换电脑后，本机完整性记录和图片缓存不会自动跟随，可能需要重新验证 XML、重新合成 PDF 或下载图片。

目录来自 [Elsevier ScienceDirect 官方期刊清单](https://www.elsevier.com/products/sciencedirect/journals) 的在刊、即将出版和出版变更表，按 ISSN 合并，保留别名并排除表中一个图书丛书条目。它是随软件发布的快照，包含更名、停刊和已转出刊名，不能据此保证某刊当前可通过 API 下载。目录搜索不访问网络，也不消耗 API 配额；论文列表和全文下载仍按原有流程请求。开发者可运行 `python build_catalog.py --source-dir .publish/catalog --download` 更新快照，打包时必须包含 `journals.json`。

## 图标

EXE、窗口和界面使用用户指定的 [Alpine 图标](https://iconic-api.onrender.com/dark/alpine)。`app.svg` 保留原始矢量图，`app.png` 用于界面，`app.ico` 包含 16–256 像素的多种尺寸，供 Windows 使用。

修改图标时，先将 SVG 渲染为至少 256 × 256 的透明 PNG，再运行 `python make_icon.py` 生成 ICO。仓库已包含这些文件，正常打包无需额外安装 SVG 渲染工具。
