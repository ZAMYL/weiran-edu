# #71 与 Research Agent：最小兼容性实测

只读基线：#71 `82f64651eb8c157ae9a9da1c2664e32321a1c4c2`。隔离探针不修改app、解析器、迁移或队友分支。

## 已实际可用

`prototypes/question_provider_probe/adapter.py` 只将精确类型QuestionAssessmentProvider签发的本地SnapshotLease转换到既有Canonical Dataset、AssessmentItem、Scale、Observation、Evidence和Kernel内部Witness。每次validate重新调用原Provider，原服务重新核验教师权限、snapshot、源字节及数据行；比较完整映射，不接受客户端JSON、伪造来源或外部模型用途。

实际合成导入20人13题：每题20个有效评分，精确均值11/4（2.75）；Q01～Q13原满分10～130保留，未映射成五维。来源引用仍指向原Provider可回放的evidence_id；内部row引用与manifest哈希仅为Kernel私有绑定，不是正式API或授权凭据。

缺一评分时：总样本20、有效19、排除1、均值仍11/4，来源只有19条。非法文本不是缺分，探针明确拒绝 `question_invalid_state_mapping_not_implemented`，不偷偷转换。

## 明确阻断

- #71只允许local_deterministic_analysis；external_model请求403 FORMAL_MODEL_CALL_DISABLED。没有AI用途授权、稳定campaign预算及持久跨进程披露承接，不能替换成SyntheticAccess、OfflineSnapshotPermit或#72 DatabaseSource。
- 原Question契约仅数值量表，没有确认的测量等级。探针保持unknown，现有Kernel拒绝相关性/群体差异资格；不擅自写成interval来让Agent运行。
- 本探针仅验证本地快照到Kernel的兼容性，不是长期持有Kernel的服务。服务工具未来必须在每次执行前后重新validate，并通过Provider累计披露门禁后再发布；本次不发布模型载荷或任何逐人数据。
- #71正式输入仍是明确13题XLSX协议，不能宣称任意题数/任意上传已经支持。映射本身按snapshot.items迭代，不硬编码13或分值。
- 现有#72教师核验/报告承接仍以正式五维发现契约为准；逐题发现、AI目标和报告本轮未开放，必须拒绝。

## 合成复现

在apps/api，PYTHONPATH=../..;.，禁用pytest自动插件：

```powershell
python -m pytest tests/edu/test_question_provider.py tests/edu/test_question_agent_compatibility.py -q
```

使用原SQLite导入确认、Provider原始文件复核与原统计Kernel，没有新增公式。测试包含错来源、撤销、外部用途、缺分和无效分数。没有模型请求、Key读取或原教师文件。
