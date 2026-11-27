# DS-04-S2 · B-02 接线

使用既有 `extract_material_input` 和 `MaterialInput`。每个 `source_id` 必须由服务端绑定到经过授权的原件版本。完成逐列外发审阅后，把当前版本、表、位置及原文对应的 `Locator` 传给资料理解入口。

```python
from app.ai.edu.material_qwen import material_review_fingerprint, understand_materials_with_qwen

# 离线生成审批绑定值，只返回摘要。
fingerprint = material_review_fingerprint(inputs, reviewed_header_labels=reviewed_labels)

plan = await understand_materials_with_qwen(
    inputs,
    reviewed_header_labels=reviewed_labels,
    require_access=require_current_grant,
)
```

`require_current_grant` 是 B-02 的服务端函数。它应校验当前操作者及每个来源版本的读取与资料理解外发许可，失效就抛出异常。适配器在预留前、发送前和返回计划前复查。模型失败或授权失效会返回原失败。调用方如需本地规则，可单独调用 `understand_materials(inputs)`。

独立审核通过并取得费用许可后，操作员在仓库外配置 `WEIRAN_EDU_MATERIAL_LIVE_APPROVAL`。沿用 Live 的代码、价格、北京 Endpoint、到期日与审核/费用引用字段，另要求：

- `purpose` 固定为 `material_semantics_remote_inference`，`source_fingerprints` 用该 purpose 作为键、上述 fingerprint 作为值。
- `ledger_root_sha256` 使用 `material_ledger_fingerprint` 计算，绑定既有 `WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT`。
- `max_requests` 为 1，一次可传多份资料。`max_output_tokens` 最高 800，`total_token_limit` 最高 992,608，`cost_cap_cny` 最高 1 元，仍受独立费用批准和有效价格限制。
- 凭据和 Endpoint 使用既有 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`。账本沿用费用预留、用量结算和一次性批准去重。

输出仍是 `MaterialPlan`。B-02 消费 `assessment`、`needs_confirmation`、`locators` 和 `capabilities`。发送的资料内容限逐列审阅通过的表头、受控字段词、匿名引用和结构计数。模型生成角色、字段和关联候选，本地仍核验这些候选。正式上传接线和教师确认写入由 B-02 负责；本模块不会启动任务 Worker。
