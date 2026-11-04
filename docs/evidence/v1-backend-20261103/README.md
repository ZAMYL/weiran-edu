# V1 后端既有验证原件

本目录于2026-11-04复制既有工件，未重跑测试；没有修改集成候选。

- 对应候选提交：`f8e13f1f54394813dc8622c8a47c5b03069a1aa9`。
- 实际测试的代码源树：`caf750a3815c454619eb85e5d4b74b1501019e87`；候选在测试后仅增加 `docs/V1_BACKEND_INTEGRATION_REVIEW.md`，没有修改生产代码或测试。
- 固定组合：main `5218af4daa621f6a39ed1055481790d708e7459f` → #65 `24c442cf9b48e11886fdaf45ac9e644b23e7c2d6` → #61 `a64e58b25f01d82aa1b2607a3aad1e0ef6b70445`。
- 本证据分支的提交只发布历史工件，不能冒称它本身重新执行了测试。

## 原始文件

- [1589项测试终端日志](pytest-full.log)：1589 passed、1 skipped、1既有warning，398.88秒。
- [原始JUnit](full.junit.xml)及[逐组汇总](full-summary.json)。唯一skip是Windows符号链接创建权限，未跳过业务验证。
- [50次真实HTTP逐请求记录](tcp-summary.json)、[验收输出](tcp.log)、[Uvicorn日志](tcp-server.log)和[原验收脚本](tcp_acceptance.py)。合成105人/525评分、17人零发现及53人正常案例，含上传、精确统计、评分依据、保存/核验、真实Markdown/XLSX下载重开、只读/撤权/快照隔离。HTTP是loopback TCP → Uvicorn/FastAPI → 临时SQLite，不是浏览器验收；原字段 `browser_executed=false`保留。
- [冲突解决记录](conflict-resolution.json)：完整#65路由加#61两处精确响应语义改动。
- [当时交付记录](delivery.json)、[检查退出状态](check-status.json)、[Ruff](ruff.log)及[格式](format.log)。历史JSON原样保留，其中“剩余条件”反映当时状态。
- [逐文件原字节SHA256与大小](SHA256.json)，复制时逐项核对一致；.gitattributes关闭这些工件的换行转换。

后续[候选CI 41778734280](https://github.com/ZAMYL/weiran-edu/actions/runs/41778734280)的Ubuntu/Windows后端和Frontend已成功；独立审核由负责人完成，作者不自批#68。实际合并基线/head变化后，以新组合检查为门槛，沿用#65→#61最小冲突策略，不重写上传或报告。

不含教师原件、数据库、凭据、真实学生记录或原始工作簿。没有真实Qwen调用、自动合并或部署。
