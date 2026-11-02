# Discovery v1 独立基准：运行与交接

实现沿用 [PR #43 的评测计划](https://github.com/ZAMYL/weiran-edu/pull/43)，没有新增 Agent 方案或改动 #41/#43/#44。此目录是可执行的离线基准，不是生产服务、真实教师鉴权或模型客户端。

## 内容

- `cases.py`：19个完全合成的固定工厂；非随机案例seed=0且明确给出构造公式，53人随机边界案例使用seed=42。覆盖17/53/105人、20人去重反例、正/负/零相关与并列、AND交互、常数、缺失/非法/不足、合法极值和少见组合，以及0/1/320/2000人。
- `reference.py`：独立Fraction/Decimal参考实现，不导入任何app/SUT/Qwen函数。Spearman以每个值小于/等于它的数量定义平均秩，以50位Decimal平方根计算相关；不是调用待测相关函数。其他统计、条件、IQR及逐指标参与者均独立计算。
- `oracles.json`：冻结的正确数值、目标资格/停止状态、禁止结论、来源数量/摘要、生成方法与数据摘要。CLI启动时核对工厂和参考实现与该文件是否一致；改变答案必须重新独立审查和版本化，不能自动接受SUT的新输出。
- `grading.py`：逐事实数值/类型、批次/快照/run/规则版本、实际参与记录及评分证据绑定、资格、重复语义、工具协议和预算检查。保留错误提议和错误展示两套计数。
- `baseline.py`：只读固定引擎适配。无引擎时明确not_run；显式外部checkout必须精确SHA、干净且使用新Python进程，不复制未合并核心进main。
- `runner.py`及`scripts/edu_discovery_eval.py`：运行A_default/B_full；C_agent默认未实现。失败照样写入JSON/Markdown，覆盖不足返回退出码1，配置错误为2；退出0只说明已运行部分没有失败，not_run仍不是通过。

本轮手算锚点包括rho=1/−1/0/5÷6、群体均值5/1差4、20人实现分组下算法差−4、53人严格4/含等号5、非零IQR围栏−1.5/2.5。测试还从原始Decimal分数独立核算4/5人数，防止参考实现本身错误。

## 运行

仓库已锁定依赖，未新增依赖。在仓库根目录：

```powershell
uv sync --directory apps/api --locked --group dev
uv run --project apps/api --locked python scripts/edu_discovery_eval.py --output D:\Temp\discovery-local.json --markdown D:\Temp\discovery-local.md
```

#41未合并的main没有discovery模块，以上A/B将明确not_run，不能据此说引擎通过。实际测试未合并的#41需使用可信源码checkout，并锁定提交：

```powershell
uv run --project apps/api --locked python scripts/edu_discovery_eval.py --sut-repo D:\AI\weiran-edu-discovery-engine-v1 --expected-sut-sha 1424655b1cf793542fe7a04de08d46f73772b95c --output D:\Temp\discovery-pinned.json --markdown D:\Temp\discovery-pinned.md
```

输出文件必须是新文件，禁止覆盖历史失败报告或写入外部SUT checkout。命令行路径是开发者选择的可信Git源码位置，不是模型工具参数；不能指向教师文件作为数据输入。程序只生成内置案例，不读取Excel、CSV、学生数据库、API Key或模型配置，也没有真实网络模型调用入口。Production未开放。

A_default使用原默认20条规则预算；B_full使用50条，保留原12项结果上限；两者最终取程序排序前3项，且获得同样明确给定的边界请求。不把oracle目标条件交给候选。操作量由cProfile观测真实函数调用数，包含实际_pair、dimension_stats、筛选与删组工作，不用外层1次调用冒充完整计算量；其额外开销计入计算耗时，不能当作无观测器性能或SLA。

## 判分口径

- 覆盖分母是独立标注的合格目标事实；无目标时为null/N/A，绝不填100%。分别记录可观察已计算覆盖与最终前3项覆盖。引擎返回被截断时，不把未返回的内部结果说成已验证；记录engine_qualified_count/returned_count。
- 数值使用精确Fraction，rho允许绝对误差1e-12；数字字符串不能冒充数字，人数必须是int，缺失不补零。所有维度对照数值均核对，不只看卡片标题。
- 引用核对逐指标实际来源及维度证据；身份/原始定位不进入报告，仅保留摘要。绑定准确率分母取应有与提供绑定数的较大值，遗漏不能免考；无绑定为N/A。事实ID引用正确率另列，不与来源绑定数量混用。
- 相同分区下不同筛选/结果维度不是重复；相同语义的反向比较规范化。数据上真实但不属于预标目标的事实记verified_extra，不能自动叫幻觉；资格不足或错误数字/来源才是不合格。
- 模型原始selected_ids与程序实际presented_ids分离；被校验器挡住的错误提议仍计数，不能宣称模型0幻觉。非法工具和越权发布、重复调用、工具/模型请求预算均独立计数。
- 协议负例只测试判分器能否识别错误、拒绝与越权记录，**没有执行真实Agent、正式ToolExecutor或教师认证**。对这些组件的动态安全验收要等后续Agent实现。
- Token/费用仅在实际qwen_live测量来源且有返回值时填；Fake/Mock不填真实用量。当前C全not_run，所有模型用量/费用均null。

## 后续 Agent 接口

```python
from evals.discovery_v1.runner import evaluate

# fixed_adapter及agent_adapter均实现run(CandidateInput, profile) -> CandidateRun。
report = evaluate(fixed_adapter, agent=agent_adapter)
```

CandidateInput只含可信本地合成数据、不可读出案例类型的上下文token、一般探索问题和明确给定边界；没有目标答案、生成公式、case标签、seed、资格标签或oracle文件。**它供服务端工具执行，不得整个发给模型**；远程聚合视图、模式/授权边界和披露限制仍须复用#43设计，本文不提供外发能力。

可信适配器提供真实工具账本Claim（Query/数值/实际来源Binding）、模型原始选择、最终展示ID、Trace与测量来源；判分器不根据模型自述重建事实。单独也可调用`grade(case, run)`。ID确实由哪个服务端执行器签发需要适配器保证，独立判分可以核算内容/引用，却不能证明一个恶意适配器伪造的执行历史是真的。

当前原始数据是合成工厂，未实施#43未来远程k/差分披露门槛；所有数据保留本地。19例是开发期合规/算术基准，**不是未见过的模型质量留出集**。不能用它证明真实千问增益、教师数据有效性、统计显著、因果或教学效果；真实Agent对照仍未运行。

## 验证

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
uv run --project apps/api --locked ruff check --config apps/api/pyproject.toml evals/discovery_v1 scripts/edu_discovery_eval.py apps/api/tests/discovery_eval
uv run --project apps/api --locked ruff format --check --config apps/api/pyproject.toml evals/discovery_v1 scripts/edu_discovery_eval.py apps/api/tests/discovery_eval
uv run --directory apps/api --locked ruff check .
uv run --directory apps/api --locked ruff format --check .
uv run --directory apps/api --locked pytest tests/discovery_eval -q
uv run --directory apps/api --locked pytest -q -ra
```

实际离线结果见`results/`；保留A_default的AND交互漏检，而非只存B_full成功。更充分搜索增加工作量，不是免费提升，也不是Agent收益。真实测试和CI结果以本PR最终提交记录为准。
