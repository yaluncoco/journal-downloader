# 期刊全文下载器

一个简洁的 Windows 桌面工具：输入自己的 Elsevier API Key，选择 ScienceDirect 期刊和发表年份，下载有权限访问的 XML 全文，并生成便于阅读的 TXT。

预置 **Remote Sensing of Environment（RSE）** 与 **ISPRS Journal of Photogrammetry and Remote Sensing**，也支持输入其他期刊主页地址。

## 下载与使用

在 [Releases](https://github.com/yaluncoco/journal-downloader/releases) 下载 `JournalDownloader-Windows-x64.zip`，解压后双击 `JournalDownloader.exe`。Windows 10/11 64 位，无需安装 Python。

1. 连接校园网或学校 VPN。
2. 输入自己的 API Key，选择预置期刊或粘贴期刊网址。
3. 选择起止年份和保存位置，点击「开始下载」。

支持的地址示例：

```text
https://www.sciencedirect.com/journal/remote-sensing-of-environment
https://www.sciencedirect.com/journal/isprs-journal-of-photogrammetry-and-remote-sensing
https://www.sciencedirect.com/search?pub=Remote+Sensing+of+Environment&date=2024-2027
```

年份以界面选择为准。更多说明见 [使用说明](使用说明.md)。

## 功能

- 圆角界面、期刊快捷切换、年份选择与下载进度。
- 暂停、继续、安全停止和再次启动后的续传。
- 通过 XML 解析、DOI、正文与 SHA-256 校验后才跳过已有文件。
- 临时网络错误自动重试，遵守服务器限流与配额等待时间。
- 默认直连 Elsevier，避免系统 HTTP 代理影响机构权限识别。
- API Key 仅在内存中使用，不写入应用设置或下载日志。
- 同一期刊按年份归档，多个期刊按 ISSN 分开保存。

## 范围说明

论文列表来自 Crossref 公共元数据，是候选列表，不保证与 ScienceDirect 的全部卷期条目一一对应，也可能包含勘误、编委会等记录。未来年份只能获取已经登记、已经发布且有权限的内容。

全文由 Elsevier Article Retrieval API 提供。API Key、机构订阅及网络出口决定实际可访问范围。本程序不绕过访问控制。本版保存 XML 与 TXT，不下载 PDF 或独立图片文件；TXT 的公式、表格和排版可能失真。

## 从源码运行

建议 Python 3.12，Windows 环境：

```powershell
python -m venv .build-env
.\.build-env\Scripts\python.exe -m pip install -r requirements-build.txt
.\.build-env\Scripts\python.exe app.py
```

## 测试与构建

```powershell
.\.build-env\Scripts\python.exe -m unittest -v test_engine test_controller
.\build.ps1
```

EXE 输出到 `release/JournalDownloader.exe`。下载引擎使用 Python 标准库；界面使用 CustomTkinter 和 Pillow，使用 PyInstaller 打包。

打包后离线自检：

```powershell
.\release\JournalDownloader.exe --self-test "$env:TEMP\journal-downloader-test.json"
```

加入 `--online` 可额外验证两个期刊的在线元数据查询，不需要真实 API Key。

## 代码结构

| 文件 | 用途 |
| --- | --- |
| `app.py` | 程序入口 |
| `ui.py` | 界面布局与主题 |
| `controller.py` | 工作线程、进度事件与设置 |
| `engine.py` | 期刊识别、元数据索引、下载与重试 |
| `xml_utils.py` | XML 校验与原子保存 |
| `test_engine.py`、`test_controller.py` | 引擎与界面控制器测试 |
| `smoke_test.py` | 打包程序自检 |

仓库不包含 API Key、用户配置或下载的论文全文。运行产生的文库、日志和构建产物已加入忽略规则。
