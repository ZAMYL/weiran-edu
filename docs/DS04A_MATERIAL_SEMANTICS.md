# DS-04A · 资料语义与分析规划

`app.ai.edu.material_semantics.understand_materials(inputs, send)` 是内部异步入口。它不打开文件、不写数据库、不执行分析、不调度 Agent。调用方给它已有抽取结果和一个已获批准、受预算约束的模型发送函数；使用 Qwen 时可封装现有 `app.ai.edu.qwen.post_chat`。它不加载凭据、分配预算、重试或签发远程准入。本轮只验证了离线响应，包括用 HTTP MockTransport 走现有 Qwen 客户端的真实编解码。

## 三道边界

1. **安全概要**：结构接口兼容 B-01 的 `ExtractedDocument` / `ExtractedTable`，无需依赖未合并分支。对抽取结果的全部行计算类型、缺失和唯一性；仅发合成位置别名、类型、计数、覆盖等级、诊断状态，以及完全通过受控词表的表头线索。原表头、表名（含工作表名）、单元格、列值及精确极值、本地路径、原诊断和可信授权信息仅在本地，或不保留；本地 `locators` 供调用方解析别名。上游如果截断或无法证明完整，应传 `partial/unknown`；即使没有警告也不默认完整。限制超出即拒绝，不用前几行假装全量。计数仍属于待审查的元数据，真实资料也需外发审批。
2. **语义建议和验证**：模型仅能给出表角色、字段含义和带两端引用的跨文件学生键建议。Pydantic 拒绝未知字段/枚举/类型，随后拒绝未知、越域、重复引用；不采信模型给的数字、自由文字或权限。类型、范围和诊断检查不等于证明语义。只有服务端绑定本次来源版本的 `TableAuthority`（可信适配器语义或已审计教师确认）可使角色/字段成为 `confirmed`。可信量表区间由上游供应，不从样本猜。相容但未证明为 `candidate`，多个解释/角色不足/身份冲突为 `needs_confirmation`，不兼容、被阻断、无键重合为 `unsupported`。
3. **分析计划**：每条 `CapabilityRule` 声明现有模块、角色、共现字段、可选关联和授权/准备度门槛。需有实际同一行的证据，且只以确认字段、完整来源和可信门槛启用；结果列明证据和缺项。它是“输入已满足”的计划，不是分析结果，更不是授权凭据。当前关联严格要求同一可信身份命名空间、完整覆盖、无缺键、精确相等的键集合，以及声明基数的唯一性；不会归一化猜测、位置拼接或暗中做部分连接。支持一对一和有方向的多对一；宽评价表、多键、不同量表及局部覆盖需要上游显式归并或后续契约。

## 运行结果

以下是运行 `understand_materials` 的实际离线输出摘要。候选 JSON 来自不同的小型契约夹具，没有调用真实 Qwen。代码中没有这些行数或列位置的产品规则。

| 输入 | 角色/关联状态 | 可用能力 |
|---|---|---|
| 编程答卷，角色和字段已确认，凭据具备 | `programming=confirmed` | 题目得分分布、DS-03 代码错误模式、测试失败现象、个体证据。无五维、无已准入 C-01 |
| 仅评价表，无可信字段/角色绑定 | `evaluation=candidate`；字段为 `candidate`、`needs_confirmation` | 无；可以人工复核字段含义，不生成代码或评分 |
| 答卷 + 评价表，已确认身份域和多对一键 | 角色已确认；关联 `confirmed: exact_join_verified_in_trusted_namespace` | 编程组能力及关联评价计划；仍无可信五维依据 |
| 同样的两张表，仅身份域不同 | 关联 `needs_confirmation: identity_namespace_unproved` | 保留独立编程能力；不关联 |

可复核的测试命令（在 `apps/api`）：

```sh
uv run --locked pytest -q tests/ai_edu/test_material_semantics.py tests/ai_edu/test_qwen.py tests/importing/test_parser.py
```

本地 104 项通过（其中新增 17 项），Ruff lint 和格式检查通过。包括表/列重排、尺度不相容、缺键和重复键、未知来源、恶意表头及单元格、不同候选、私有上下文不出站。

## 最小交接

| 对象 | 需要提供或消费的最小接口 |
|---|---|
| B-01 / B-02 | 交付抽取后的结构，按实际解析结果声明覆盖、被阻断的表/列和版本；未确认角色不要写入 authority。答卷来源可将 B-01 已解析的角色、代码状态、判题和测试列映射到结构表，不重读 ZIP。评价可先给本地预览候选，教师确认后绑定本次快照。现有 B-01 通用抽取有上限，不能在没有覆盖证据时直接声明 complete。 |
| B-03 | 提供稳定、版本绑定的来源/字段定位，以及真实的权限检查回执和身份域。不能把普通文件名或相同键文本当身份域；映射最后仍由上游持久化和审核。五维门槛应绑定已有 Canonical Dataset/Readiness 对五个既定维度、有效量表、原评分和可用依据的证明；DS-04A 不计算五维。 |
| DS-03 / C-01 | DS-03 仅从可复核且受支持的代码证据产生特征/错误模式。C-01 仍从其正式 EvidenceProvider 获取判题、测试、Witness 和可选特征，不读取此处模型文本；须由 B-04 正式准入后传入 `c01_admitted`，不可因计划可用而绕过权限。 |

参考对象：B-01 [#105](https://github.com/ZAMYL/weiran-edu/pull/105) `ec0e1e3`、DS-03 [#108](https://github.com/ZAMYL/weiran-edu/pull/108) `b929787`、C-01 [#107](https://github.com/ZAMYL/weiran-edu/pull/107) `ce8fb6a`。本 PR 基于 main，未合并这些参考实现。正式适配、审批、数据库回读和模型质量留待联调。

## DS-04-S1 调用

```python
from app.ai.edu.material_contract import MaterialInput
from app.ai.edu.material_semantics import extract_material_input, understand_materials

# 二选一。source_id 由服务端生成并绑定原件版本。
material = extract_material_input(data, source_id=asset_version)
material = MaterialInput(extracted_document, source_id=asset_version)
plan = await understand_materials((material,))
```

可以一次传入多个 `MaterialInput`。默认使用 DS-04B 的本地规则。已有独立外发授权和预算时，
服务端可以传入 `send=` 回调，候选仍走同一引用、类型和关联校验。回调只接收安全概要。

B-02 消费 `assessment.roles / fields / links` 的状态、原因和证据；
`needs_confirmation` 列出仍待核查的位置，包含规则或模型弃答的有数据列。
使用 `locators` 将引用指回当前抽取结构的来源版本、工作表和列，索引从 0 开始。
`capabilities` 的 `available` 及 `missing` 反映实际字段和准入证据。
从文件构造的输入默认覆盖度为 `unknown`，且没有 `TableAuthority`。
仅上游已验证的映射、覆盖、身份域和权限可以通过可信服务端输入提交。
本轮定向验证 XLSX 与 CSV，正式上传、人工确认写入和 SQLite 入库仍由后续接线提供。
