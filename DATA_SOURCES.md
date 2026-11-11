# v1.5.0：期刊数据与设计依据

资料核对日期：2026-11-06。本文件随 v1.5.0 源码和发行包提供。

## 目录与学科

- 期刊目录沿用 2026-11-04 的 Elsevier 官方 ScienceDirect 清单，5,211 个按 ISSN 合并的刊名，含在刊、历史及转出记录。
- 官方入口：https://www.elsevier.com/products/sciencedirect/journals
- 学科原表：https://legacyfileshare.elsevier.com/promis_misc/sd-content/journals/jnlsubject.xlsx
- 2026-11-06 获取学科表，按 ISSN 匹配到目录内 5,147 个刊名；原始表的一级、二级、三级分类中，保存 Primary Level 和 Secondary Level。
- 农学与生物科学指 Agricultural and Biological Sciences；林学指 Secondary Level = Forestry；计算机指 Primary Level = Computer Science。交叉筛选要求同时具有相应标签。
- 因而 Computers and Electronics in Agriculture 可进入农学×计算机、林学×计算机。RSE 等遥感刊物不会仅因可能发表林业应用而被推断为该交叉类别。这些标签用于发现期刊，不代表所有论文主题或投稿范围。

## 2025 中科院分区参考

- 参考项目：https://github.com/hitfyd/ShowJCR
- 固定提交：`4687a8f608f20aaf004299992f5d347dba009361`
- 数据文件：`中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv`
- 源文件：https://github.com/hitfyd/ShowJCR/blob/4687a8f608f20aaf004299992f5d347dba009361/中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv
- CSV SHA-256：`481224dca2cacc1cce49afb44c968d734c834e498e5ff054f15efb5a548789fc`
- 上游 README 自述中科院数据来自 `advanced.fenqubiao.com`。本次未能取得官方逐刊核验，因此界面始终标为 **2025 中科院参考**。
- 只读取年份为 2025、分区为 1–4 的条目，按 ISSN/eISSN 精确连接；匹配到目录中的 2,704 个刊名，无冲突匹配。不会用模糊刊名猜测分区。
- 显示大类、分区、排名、Top、收录索引。SCIE/SSCI 等索引按原表展示；**中科院分区不是 JCR 四分位**。没有匹配记录时显示“未收录”，不代表未被 SCI 收录。
- 分区是固定年度参考快照，不进行实时在线验证，也不等同于 2026 新锐分区。正式评价请核对机构认可的年度分区表。
- 上游仓库采用 GPL-3.0；附上原许可证 `data-licenses/ShowJCR-GPL-3.0.txt`。本版参考数据的转换记录在 `build_metadata.py`，来源、版本与校验值保存在 `journal_metadata.json`。未复制上游应用代码。此来源说明不表示上游数据已获分区表官方授权。

## GitHub 示例如何用于本次设计

| 项目 | 参考点 | 本版采用方式 |
| --- | --- | --- |
| hitfyd/ShowJCR | 年度指标分表、来源说明、ISSN 识别 | 固定 2025 年与提交版本；数据独立于界面；未知值明确显示 |
| https://github.com/hanxiangmin/zotero10-scholar | 分清大类分区与其他指标；保留未知匹配 | 标明中科院参考、Top、索引；不猜测未匹配刊物 |
| https://github.com/rapidfuzz/RapidFuzz | 显式预处理后再进行文本匹配 | 本版无需引入模糊检索依赖；优先正式刊名、顺序前缀、连续词组，再匹配分散词 |

`computer and` 同时匹配多个以 Computers and / Computers & 开头的期刊，无法唯一确定一个刊名。检索已统一 Computer/Computers、and/&；继续输入 `computer and electronic` 时，Computers and Electronics in Agriculture 排首位。正式刊名与别名分开评分，避免别名污染准确匹配。

## 流程与代码边界

- 下载与单独合成共用 `PdfExporter.export_one()`；逐篇模式不会每下载一次就重扫全部 XML。
- `article_filter.py` 负责下载前题名筛选；完整候选索引不因筛选而被覆盖。词组以逗号、分号或换行分隔；英文按完整词/词组、不区分大小写匹配，连字符视为空格。
- `download_output.py` 负责输出选择及安全清理。PDF 的哈希、页流、DOI 与校验记录通过后才移除相应 XML；失败、停止或写入异常保留 XML。
- `pdf_inventory.py` 负责实际 PDF 的完整性盘点，`library_preview.py` 负责后台扫描和丢弃旧目录返回的结果。无校验记录的 PDF 不算已核验完成。
- 本次下载数量按最终要求的完整输出计数。仅 PDF 模式依靠本机校验记录与 PDF 内容续传，不能仅凭文件存在跳过。移动目录或换电脑后校验记录不会跟随，可能需要重建。
- 期刊目录检索与分类离线进行；论文关键词可选择 ScienceDirect 在线 API 或本地题名筛选。自动化回归使用合成数据和模拟网络；真实接口探测只检索元数据，不下载全文。

## ScienceDirect 论文关键词检索（v1.7.0）

实现核对日期：2026-11-11。采用 Elsevier 推荐的 Search V2 原生 PUT 接口；无浏览器抓取、无 Scopus 结果替代。

- 接口文档：https://dev.elsevier.com/documentation/ScienceDirectSearchAPI.wadl
- 字段、语法、分页、请求/响应示例：https://dev.elsevier.com/tecdoc_sdsearch_migration.html
- 字段和授权说明：https://dev.elsevier.com/sd_search_views.html
- 请求地址：`https://api.elsevier.com/content/search/sciencedirect`。
- `qs` 搜索内容（不含参考文献），`title` 搜索文章标题，`pub` 限定刊名，`date` 限定年份；`display.offset/show/sortBy` 用于分页与排序。布尔运算、词组与语言处理交由服务端执行。官网核对入口使用相同条件，不承诺网页与 API 在任意权限、索引时刻下条数相等。
- 刊名使用引号词组并检查响应刊名及目录别名，防止未加引号的词语检索混入其他期刊。响应无有效 DOI 或年份时不猜测下载地址，排除情况写入运行记录。
- 完整查询以条件哈希独立缓存；没有响应或权限失败不计为零结果；超过软件单次完整获取上限 6,000 条时要求缩小检索范围，不缓存截断列表。
