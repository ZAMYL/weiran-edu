# 自适应编程测评分析原型

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

状态：独立合成技术原型，可执行两种结构；不是正式 V1 导入能力，也不是已验证的真实 Qwen 效果。
依赖 main `761d209`（含 #23/#41）和 #49 安全头 `91de608`。#49 未合并，当前 PR 先以其分支为基线；不覆盖父 PR 或 #40/#50，不绕过父 PR 审核。
#47 的冻结标准答案、原始基线及测试保持原样。本原型不是完整 C_agent 评测接入。

## 实际运行

在仓库根目录，Python 3.12、使用现有锁文件，无新依赖：

```powershell
uv sync --directory apps/api --locked --group dev
uv run --project apps/api --locked python scripts/edu_adaptive_demo.py --output D:\Temp\adaptive-new.json --zip-output D:\Temp\adaptive-new.zip
uv run --directory apps/api --locked pytest tests/adaptive_assessment -q
uv run --directory apps/api --locked pytest tests/ai_edu tests/discovery ../../docs/canonical_assessment -q
```

CLI 只生成内置合成场景，不接收教师文件路径；输出必须选择新文件名，不覆盖既有文件。
完整实际运行记录：`prototypes/adaptive_assessment/offline_demo.json`。
完全合成归档：`prototypes/adaptive_assessment/fixtures/synthetic_contest13.zip`，可由 `contest_zip()` 逐字节复现。
该 ZIP 是明确生成的测试资料，未放宽全局归档忽略规则，没有任何真实教师/学生文件。

## 两种数据及不同实际路径

| 输入 | Canonical 结构与事实 | Fake/HTTP Mock 实际路径 | 最终合成结果 |
| --- | --- | --- | --- |
| 五维影子投影 | 53 人、5 competency、265 observations，原量表1–5/0.01 | 概况 → I01/I02相关性 → 群体对照 → 依据复核 → finish | 成对n53、rho=-1；I01≤1的26人与余组27人，I02均值5/1，差4 |
| 13题 ZIP/HTML | 20人、13 question、260 observations，逐题0–100/0.01 | 概况 → Q13质量复核 → 群体对照 → 依据复核 → finish | Q13缺5/20，实际15个有效分均值60；Q01≤20的10人与余组10人，Q02均值80/20，差60 |
| 相同13题方案但补齐缺分 | 项目类型、题目数量、量表均相同，评分记录不同 | 概况 → Q01/Q02相关性 → 群体对照 → 依据复核 → finish | rho=-1；路径变化由实际质量计数触发，而非case名/题型脚本 |
| 缺资料 | Q11的20条评分缺可信评分证据，Q12量表未知，Q13缺5分 | 仍可探索获支持项目，复核未知量表现象；不使用受限项目生成关系或筛选 | Q11有效n0、均值null；Q12原始数值均值50但range_checked=false；不生成能力等级或评分正确性结论 |
| 3人小样本 | 保留13实际题目，不补造评分 | 概况 → finish | insufficient_data，零卡；模型通道不披露小格精确统计 |

每个正常场景5次Mock模型请求/4次工具；小样本2次/1次，真实外部模型调用均0。
完整 Trace 分开记录 planning、model_selection、tool_execution、fact_validation、下一轮决策与 final_selection。
脚本化攻击测试不是自主规划；Fake 是透明的结果驱动启发式，仅证明机制和分支执行，不能证明 Qwen 理解或优于固定流程。

## 输入协议：仅合成 XHTML-in-ZIP v1

仓库只有“竞赛答卷/逐题”概念背景，没有可核验的历史 ZIP/HTML 文件协议。本次**不宣称兼容实际历史答卷**。

归档允许：一个 `manifest.html`，以及0–100个 `answers/Sdddd.html`；项目1–64项，实际题数以manifest为准，不固定13。
严格UTF-8、良构HTML/XML子集：

```html
<html data-protocol="edu-synthetic-contest-v1">
  <head><title>Synthetic assessment</title></head>
  <body><table id="items"><tbody>
    <tr data-question="Q01">
      <td data-field="min">0</td><td data-field="max">100</td>
      <td data-field="step">0.01</td><td data-field="unit">points</td>
      <td data-field="level">interval</td><td data-field="criteria">显式合成标准，或空</td>
    </tr>
  </tbody></table></body>
</html>
```

答卷table为`id=answers, data-participant=Sdddd`，每个声明题目恰好一行，td顺序为score/max/response/evidence。
score空表示缺失，不是0；response仅普通文本，不执行、不发送模型。题目整行缺失则拒绝此合成协议，不静默补行。
evidence是合成夹具的available/withheld/unconfirmed/absent声明，**不是实际教师授权机制**。
标准为空就没有对应Rubric；示例只有Q01真正提供了标准。未知满分/范围/步长必须三者均空，原值可做受限描述，不能解释等级。
每行满分必须与manifest一致；重复/未知题目、匿名主体与文件名不符、来源范围冲突整份拒绝。

安全边界：压缩输入≤1MiB、展开≤4MiB、单部件≤64KiB、≤101文件、压缩比≤200；只支持Stored/Deflated普通文件。
逐条受限读取并检查CRC；拒绝路径穿越、反斜线、绝对路径/流、重复、特殊文件、未知部件、加密、未知压缩、前后夹带。
XML禁止DTD、实体与外部引用，固定标签/属性/层次；拒绝script、链接、样式、事件属性和嵌套响应HTML。全程不解压到磁盘、不浏览、不执行答卷代码。
限值是本原型资源边界，不代表正式导入能力。合法历史文件也可能因协议不同被明确拒绝；实际兼容需另取经脱敏和授权的协议样本。

## 标准实体与证据

直接使用 #23 `docs.canonical_assessment.model` 中的 Dataset/Batch/Participant/AssessmentItem/Observation/Rubric/Evidence，以及原validate、score_state、assess；没有复制另一套Canonical模型，也没有改其A–H夹具。
Envelope只补充原始字节、SHA-256、测量水平和本地证据Witness。它不是正式存储结构或公共DTO。
每次工具调用重核内置工厂的确定性输入指纹，并重放原始字节得到Canonical对象及来源；任意上传数据/改过的资料不能靠synthetic=True进入Agent。
Witness绑定源摘要、条目摘要、题目、匿名参与者、字段定位及内容摘要。缺评分证据时，缺口计数只能引用实际行声明，不能冒充有效评分证据。

`from_five_dimension`只影子投影已有验证过的内存五维记录，保留精确分数和原合成评分定位，不解析Excel。
其SHA是合成内存事件的摘要，**不是原始Excel字节摘要**；正式接入须由后端提供真实文件hash及证据服务，不能由此假冒原文件溯源。
题目始终是question，不建立五维Profile映射，不截成五项、不求能力总分。不同满分保持原尺度；相关分析用各自排名，均值差只在同一结果项目内计算。

## 分析与资格

- 统计：按原项目分别使用Fraction、stdlib mean/median及频数统计。缺失、非法范围/精度、缺证据、来源未确认、用途withheld分别计数，不当零分。量表未知仅描述原数值并标range_unknown。
- 关系：复用 #41 `average_ranks` 与相同stdlib Pearson-on-ranks实现Spearman，正确处理并列；要求已知数值量表/顺序前提、至少5个有效成对样本，常数返回不可计算。记录N、有效成对n、排除数及实际来源。
- 群体：最多两个AND条件，严格区分<与≤，阈值按原量表范围/精度校验。目标与可判定余组互斥，M/R/U/N/E分开；条件项目不能充当结果项目。
- 资格：复用 #41 Policy、ranking/status及三次固定删组口径；动态项目的探索效果阈值为原0.5/(5−1)所对应的量表跨度比例1/8。仅用于各结果自身尺度的探索资格，不转换或比较不同项目的“能力分”，不是教学标准或显著性检验。
- 质量：复核缺分、非法、来源不可用和范围未知等实际状态；缺标准只标不可判断，不声称核查正确，不自动改分。空/不足样本不能生成合格质量卡。
- 来源校验：计算后使用从原始字节重建的Kernel重放数值与逐指标实际来源，再原子发布；finish/resolve再次核验视图及来源摘要。即使证据ID存在，错误参与者/项目/数值也不能通过。

原型没有重新实现五维prepare_dataset或教师评分规则。通用统计使用标准库原语，与V1的Fraction数值和5/1差4、n53等独立预期核对；原有发现内核数值不变。

## 共用Agent安全底座

从 #49 提取相同run_session与check_disclosure，共用6次模型请求、5次工具尝试（含失败）、时间/字节上限和不可跨run引用的事实ID。
旧run_agent仍先验证原封闭工厂/Offline模式，旧Schema、两阶段run_insights、Production限制不变；新原型的工具参数使用实际item_id。
同次工具的所有暂存集合参与累计差分检查，默认k=5；任一后期错误不提交部分事实、来源或输出字节。该规则不是差分隐私证明，不处理跨run联合推断。

模型只接收实际项目ID/受控标签/量表元数据及明确字段的聚合事实，不接收个人评分行、细分散点、评语、答卷/标准原文、来源定位、证据ID或凭据。
全项目概况保留13项；详细分布最多按需查询3个项目，不提高8KiB单工具/32KiB累计/64KiB请求限制。小格导致整个分布抑制，不能从其余格补算。
最终模型仅选已核验且合格的fact_id；标题、数字、条件和图表由程序生成。真实来源仅在本地受控resolve中返回，未提供公开分享、真实鉴权或HTTP下载。

## 旧五维Agent与原型的差别

| 问题 | 原固定五维Agent | 自适应合成原型 |
| --- | --- | --- |
| Q01/Q02参数 | Dimension Literal不接受；不能伪装成ALGORITHMS | 根据真实题目表生成工具Schema枚举，服务端仍校验 |
| 13题0–100 | 原评分策略1–5，不能直接套用 | 保留13题各自尺度，按题统计、配对、互斥对照 |
| 缺量表/证据 | 不应靠五维默认值补造 | 就绪元数据限定能力，原始描述与不可执行推断分开 |
| Agent路径 | 只支持固定模型 | 同一工具执行预算下，实际质量计数改变调查方向 |
| 已证明内容 | 既有五维算法及机制回归 | 双结构执行、来源核验和离线分支；尚无真实模型质量增量证明 |

本原型没有做历史格式识别、自由语义映射、通用文件平台、OJ、题目知识点诊断、AI协作能力、成长追踪或因果教学效果推断。

## Synthetic Live准备与未完成项

原生Function Calling HTTP Mock复用既有Qwen受限sender，提供相同动态Schema；默认模型标识仍为qwen3.8-flash，未核验实时可用性。
`SyntheticLivePlanner`协议与`prepare_synthetic_live`可准备受控工具规范；即使传入声明的LiveAuthorization，也只返回prepared_remote_disabled，不创建网络客户端、不读取环境凭据。
真正Live还需要：经审核的Synthetic Live专用客户端、明确的人类场景/总请求数/Token/人民币费用封顶授权、账户地域/业务空间端点与模型可用性核验，以及实际usage/单价不确定时fail closed的成本保护。
每次run6/5、每次输出800 Token是工程上限，不是本轮付费授权。当前Token/费用均null，network_model_calls=0；Production始终关闭。

## 正式接入下一步

1. ZAMYL/后端共同确认实际历史竞赛结构协议、逐题评分语义、量表/测量水平与合法证据；单独审核适配器，不扩大V1 Excel准入。
2. 彭仲楷提供已授权、同快照、来源完整性核验后的Canonical Provider，并区分评分使用与外发聚合授权；本次不读SQLite或改变正式DTO。
3. 正式证据下钻重新检查教师/批次/快照/授权撤销，转换为受控evidence_id；本地Witness/SourceQuery不能成为权限凭证。
4. 林明未来展示recognized_items、capabilities、fact/discovery_id、批次/快照、metrics/分母/conditions、status/limitations及受控下钻入口；不能给question套五维雷达。
5. 真实Qwen合成测评另行授权，并独立评估覆盖、误报、披露抑制、预算及Token；不把本报告的Fake/Mock路径称为模型效果。

## 验证记录

- 新原型专项82 passed（40.36s），包含ZIP/XHTML、来源/数值篡改、动态项数、不同尺度、AND/边界/缺分、两种结构与同结构分支变化、Native HTTP Mock、预算与Live禁用。
- 最终#23/#41/#49加原型联合回归511 passed（94.60s）。
- 本地首轮全量1332 passed、1 skipped、1 warning（861.48s），采集的是当时79项原型测试；之后新增的3项安全/质量门测试已包含在上述82/511通过结果，最终提交完整测试集合另由CI核验。
- 本地跳过因Windows符号链接权限；警告为既有Starlette/httpx弃用提示。
- 新目录位于原CI lint范围外，因此新增pytest质量门显式运行原型Ruff/format；不修改共享工作流。
- 最终结果记录在本PR，未测试部分不冒充通过。真实历史资料、真实Qwen、真实教师权限/页面接线均未测试/未实现。
