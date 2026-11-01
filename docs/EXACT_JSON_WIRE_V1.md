# EDU精确整数响应契约 v1

2026-11-01。范围为#61的正式发现/保存响应；#65上传契约保持原样。接口URL、方法、业务字段和请求参数不新增。正式教师资料仍仅本地确定性分析。

## 1. 确定的编码与兼容策略

JS安全整数上限为 `9007199254740991`，对分子、分母**分别**判断：

| 字段 | 正式响应编码 | 限制 |
| --- | --- | --- |
| numerator | 绝对值≤上限保留JSON整数；超界使用有符号十进制整数字符串 | 最多1024位数字，负号不计位数 |
| denominator | ≤上限保留JSON整数；超界使用正十进制整数字符串 | 必须>0；最多1024位数字 |

字符串只包含ASCII数字及可选的分子负号：无前导零、加号、小数、指数、空白或Unicode数字。零分子输出数字0，不输出字符串`"0"`或`"-0"`；字符串分量必须确实超出安全范围。因此分子字符串最多1025字符，分母字符串最多1024字符。OpenAPI的实际响应Schema包含integer|string、数值边界、规范字符串pattern及长度限制。

正常旧响应不变：

```json
{"numerator":31,"denominator":10,"display":"3.1000"}
```

林明固定种子20261031的105人样例修复后字段应为：

```json
{
  "ranking": {
    "score": {"numerator":"24840307107174983","denominator":"25000000000000000","display":"0.9936"},
    "effect": {"numerator":"9840307107174983","denominator":"10000000000000000","display":"0.9840"}
  }
}
```

以上是字段片段，不是省略其余必填字段的完整DiscoveryDetail。不同夹具或不同授权快照可能得到不同ranking，不能把例值当硬编码答案。

## 2. display的准确规则

DiscoveryExact沿用现有内核生成规则：`f"{float(Fraction(numerator, denominator)):.4f}"`，即Python3.12对有限binary64值的四位定点格式，不改变为另一种取整算法。格式为 `^-?(?:0|[1-9][0-9]*)\.[0-9]{4}$`，最多320字符；不得出现NaN、Infinity或指数。非有限/无法生成原有限显示的值拒绝输出。

序列化边界检查display与上述原规则完全一致；不会静默修正不一致的字符串。保留原行为允许小负数显示为`-0.0000`，这不是允许整数分量字符串`"-0"`。display仅用于展示，不用于还原精确值、排序、资格、筛选、分母、来源或事实验证；不能用合法display掩盖非法分量。

普通StatValue的display仍使用既有两位小数、整数运算HALF_UP；它的exact为不带display的双分量对象。不得把DiscoveryExact的四位规则套在外层StatValue上，也不要求前端用JS toFixed重新模拟Python显示。

## 3. 适用范围

编码在严格Pydantic响应DTO验证完成后进行，递归覆盖：

- 发现详情的ranking（score/effect/support/completeness/robustness.value）、metrics、chart、conditions.threshold、parameters。
- 发现列表、详情、图表及群体响应的scope.statistics内精确值；现有统计响应的ExactRational。
- 保存、回读、核验结果中的 `analysis.discovery.findings[]` 与 `discovery_findings[].discovery`，以及同一结果中的统计精确值。

只对完整的 `{numerator,denominator}` 或 `{numerator,denominator,display}` 对象采用该编码，不能按单独denominator键把人数改成字符串。普通计数、人数、分页、核验版本保留原合法安全整数类型；不安全的非exact整数拒绝输出，计数/分页/版本的原非负或正数语义继续保留。字符串形式的规则/预览版本不受影响。

评语/依据尚未核验时的版本0是合法值，必须保留；保存分析的正版本和各expected_version仍按原DTO处理。不能把所有名为version的字段都当作必须大于0。

内部分量仍为Python int/Fraction；Pydantic内部model_dump/model_dump_json、数据库payload、SHA、快照、input_key、发现ID、计算和排序均不改。不能只在presentation.exact()中改字符串：严格模型会拒绝，而直接改模型持久化serializer又会影响旧payload/hash。本实现只改变HTTP响应边界。

## 4. 错误与预算

- 类型错误、零/负分母、分量超过1024位、display非法或不一致、不安全普通整数：不返回被截断/舍入的成功值；HTTP编码边界返回 `503 DATA_INTEGRITY_ERROR`，沿用统一错误结构、X-Request-ID、Cache-Control:no-store，不回显原值。
- 发现的原1MiB响应预算保留，并按**最终线编码字节数**核算；超限继续 `413 DISCOVERY_RESPONSE_TOO_LARGE`。不扩大计算或请求超时。
- 正常旧数字仍兼容；旧前端不会理解超界字符串，新前端不得接受旧后端已经不安全的Number。必须部署配套版本，不能把HTTP200作为解码成功的依据。

本契约不新增接收客户端ranking/统计事实的请求接口。请求参数继续原验证规则，不从客户端display或字符串重建正式评分。

## 5. 给林明的解码要求

```typescript
type ExactInteger = number | string;
type DiscoveryExact = {
  numerator: ExactInteger;
  denominator: ExactInteger;
  display: string;
};
```

先检查类型、格式和1024位预算，再做BigInt；数字分量必须Number.isSafeInteger，字符串必须是规范、超界的整数，分母必须为正。临时BigInt不放进JSON.stringify对象，不先用Number/parseFloat转换大整数字符串。保留普通count/分页/version解码器及全部权限、来源、快照校验。ranking不是统计置信度，`is_statistical_confidence`仍为false。

## 6. 下载兼容与验证

显式Response文件下载不经过ExactJSONResponse。XLSX/Markdown的事实、精度、排版、公式注入防护、敏感过滤、最终权限/快照/版本/发布文本核验保持原行为。私有JSON/ZIP继续原归档整数格式；这些旧归档不能直接视作本次浏览器DTO，普通JSON.parse仍不能保证无损读取归档大整数。

`tests/edu/test_exact_json.py`与`test_exact_contract.py`覆盖真实FastAPI/Pydantic响应、安全边界、1024位正负分量、非法内部类型、零/负分母、长度、display一致性、计数、固定错误、OpenAPI及保存/核验/四种导出和原payload/hash。后续仍须在#61+#65组合及林明#40完成105人真实HTTP/浏览器闭环；单分支专测通过不代替组合验收。不自动合并PR。
