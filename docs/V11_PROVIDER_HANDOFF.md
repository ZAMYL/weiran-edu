# V1.1 正式快照 Provider：本地确定性接线

模块：`apps/api/app/edu/question_provider.py`。这不是Research Agent正式HTTP入口，不修改Planner、统计Kernel或模型调用器。仅支持本PR明确十三题XLSX产生的SQLite快照；五维既有Reader继续使用，尚未映射为这个Provider。

## 最小内部契约

```python
provider = QuestionAssessmentProvider(app.state.edu_service)
lease = provider.acquire(server_principal, batch_id, snapshot_id)
snapshot = provider.read_snapshot(lease)
witness = provider.replay_witness(lease, reference_id)
receipt = provider.commit_disclosure(lease, reference_groups)
provider.release(lease)  # 停止/取消时关闭lease，不清空历史
```

`server_principal` 必须是既有认证链生成的Principal，Provider再次查询真实教师token、活动状态、教学空间成员与批次拥有者/只读授权。不能传入客户端JSON、任意Dataset/Envelope、客户端评分、self-reported权限或伪造lease作为来源。

Snapshot冻结实际batch_id/snapshot_id、原文件SHA256、实际成员/项目/用途/规则的范围摘要，包含Question（原题名、question类型、原量表及Sheet1来源范围）、Observation（原词法/精确Fraction/独立质量状态/Sheet2位置）和SourceWitness。未知项目或来源不能靠名称猜测。没有提供量规正文或答卷，因此这些能力仍不可用。

Witness绑定reference_id、batch/snapshot、原字节摘要、参评记录、item_id、Sheet2及原单元格；其Observation和整个Snapshot都仅供内部服务使用，repr不输出受控内容，不直接序列化给模型。每次read/replay/disclosure都在授权SQLite事务中重建原文件并与全部存储事实、评分来源ID和快照对账；撤权或源损坏立即拒绝。

Lease由当前服务实例签发，服务器保存对象身份；dataclass替换、复制或JSON重建不构成合法lease。取消后的旧lease拒绝。重新取得lease或重新创建Provider对象不会清空同一EduService内的主体/批次/快照/用途披露上下文。

披露输入是当前快照的合法Witness引用组，不接受客户端自报人数或成员集合。复用现有check_disclosure（最小单元5）并进一步检查多个已披露集合产生的交集/差集原子，阻止单次群体都足够大但累计形成小单元的绕过。先检查再提交，失败不改变历史；每上下文最多256组，单次32组。

## 已明确阻断的能力

- `purpose="external_model"` 等非本地确定性用途返回403 `FORMAL_MODEL_CALL_DISABLED`。Snapshot明确 `remote_model_allowed=False`，没有付费调用入口；即使模型价格/批准有效，也不改变本规则。
- 当前披露状态在单个受控EduService进程内共享。跨进程、跨重启持久披露及可撤销的分布式调用授权尚未实现；旧lease跨服务实例不准入。不能因此宣称正式模型调用已安全接通。
- 当前Agent #67只接受注册合成AdmittedSynthetic/Envelope；本模块返回可信服务端Snapshot，不伪装成合成recipe。Agent负责人须审核并实现显式来源适配，不能直接放宽为任意JSON、把registered synthetic改个标志或跳过Witness重放。
- 未来正式模型接入另需准确代码和独立人工批准、用途/最小披露投影、跨进程持久累计披露及预算、取消与撤权闭环，以及正式HTTP DTO和错误/停止状态。现有#66/#67的付费门禁问题独立处理，不能用本Provider交付替代其复审。

已向ZAMYL在#67同步上述最小契约，待Agent侧确认与适配；未声称双方联调完成。现阶段可以先在后台用授权正式快照作本地确定性统计及来源核查，不能把原始记录、评语、评分或文件发送Qwen。

## 合成验证

真实FastAPI/SQLite导入的20人十三题快照：检查13个原量表、260条观测及Witness；拒绝客户端JSON、错误快照、未知/替换来源、伪造lease、未授权主体和external_model用途。只读授权可读，撤权或撤销token后read/replay/disclosure拒绝；新Provider及新lease沿用历史，多个群体形成小交集时拒绝；release后旧lease拒绝且历史不回退。全程无真实教师资料、模型、付费调用。
