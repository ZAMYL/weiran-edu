# 正式安全依赖同步（全部Live仍关闭）

#66 445f3e2086c90fc0ba8c7271a15d433ffa0c2689已获Peng-Kai789在准确SHA的正式APPROVED；该批准不等于本PR批准。

本次在原#67分支选择性应用隔离候选ef89069的最小补丁：budget、旧CLI、Planner、Research demo/runtime统一拒绝Live；保留PilotApproval、价格快照、固定campaign、最坏预留/用量结算、Fact与Oracle。review依赖固定为已批准445f3e2，不伪造本分支APPROVED。

历史政策迁移：隔离候选曾75 passed/2 failed，原因是旧正例要求Live构造成功，与全面封禁冲突。已将两项改为必须拒绝、Key读取0、Live账本副作用0；未skip/xfail/恢复Live。隔离候选后续78 passed；这些不是本次新SHA的测试/CI。复制授权反例现同时覆盖旧价格和2026-11-04价格有效日，不用过期掩盖旧旁路。

CI继续Ubuntu全量与原Frontend；Windows按当前SHA完整collect分成五个整文件片，每片15分钟。完整nodeid、实际三阶段结果、SHA、计数保留为工件；汇总拒绝缺片、重复、跳过、xfail、未知参数与错SHA。唯一稳定身份映射是原测试导入时uuid4生成的两个参数，保留原始记录和reader分支、拒绝映射碰撞；空目录片标not_run_empty_shard，不能执行全套冒充该片。没有硬编码1675或删断言。

统计、评分、独立输入/Oracle、正式教师API/数据库/页面及任务契约均未改变。真实Key读取与模型请求0。最新SHA测试与CI在PR中逐次记录，不拿历史CI放行。依赖代码变化仍请Peng-Kai789独立复审，不合并或部署。

本次提交前定向验证：research_v22 + adaptive test_qwen_live + Windows审计单测，86 passed（147.24s）；后端Ruff/format 201文件通过，原型及新脚本通过。远端准确SHA全量CI尚待执行，不计作成功。
