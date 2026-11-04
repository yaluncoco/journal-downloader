# IEEE 接口核对

核对日期：2026-11-04。

## 当前测试结论

使用待审批的测试账号，向官方 Metadata Search API 发出一次查询请求，服务端返回 **HTTP 403，`Developer Inactive`**。账号所有者确认申请尚未获批。本次未继续请求全文或 PDF，也未消耗配额反复重试。

因此当前版本不把 IEEE 显示为可用下载来源。需要账号获批后，再依次验证元数据、全文访问和返回格式；不能将现在的失败解释为“IEEE 没有全文 API”。仓库及程序不包含测试账号的 API Key。

## PDF 与 XML 的区别

官方文档列出 Metadata Search API、Open Access API 和 Full-Text Access API。元数据查询默认返回 JSON，也可请求 XML；把元数据的返回格式设为 XML，并不会将摘要变成论文全文。

全文示例需要文章编号，使用如下结构（示例不含真实 Key）：

```text
https://ieeexploreapi.ieee.org/api/v1/search/document/{article_number}/fulltext?apikey={YOUR_KEY}&format=xml
```

元数据还可能返回 `pdf_url`，文档将它描述为官方全文 PDF 链接。这个字段说明存在 PDF 地址，不等于当前 API Key 已获得该文档的 PDF 下载权限，也不保证该地址直接响应 PDF 二进制。是否能直接批量保存 PDF，需要在账号获批后核对实际响应和相应权限。

后续处理原则：如果实际接口能返回完整官方 PDF，则优先保存并校验官方 PDF；如果只获得结构化 XML 全文，则先核对 IEEE 的结构、图像来源和权限，再适配合成器。当前 Elsevier XML 合成器不会将未经适配的 IEEE XML 误判为可完整还原。

## 请求限制

测试账号后台显示：每秒最多 10 次、每天最多 200 次。这是该账号提供的限额，不作为所有 IEEE 账号的统一限额。

在后续启用 IEEE 接入时，计划使用每 10 秒最多 1 次、每天最多 100 次的保守默认值（用户已选择），并允许依据账号配额调整，但不得超过服务器限额。需缓存论文列表和已下载文件、记录本地请求计数、遵守服务器等待时间。当前版本没有运行 IEEE 批量请求。

## 官方参考

- [接口总览](https://developer.ieee.org/docs)
- [申请与审批流程](https://developer.ieee.org/Quick_Start_Guide)
- [查询结构](https://developer.ieee.org/docs/read/Searching_the_IEEE_Xplore_Metadata_API)
- [返回字段、全文示例与 pdf_url](https://developer.ieee.org/docs/read/Metadata_API_responses)
- [访问用途与全文权限](https://developer.ieee.org/docs/read/IEEE_Xplore_Metadata_API_Overview)

官方快速入门说明：API Key 需要 IEEE 审核账号信息后发放；实际请求限制以账号后台显示的 Rate Limits 为准。
