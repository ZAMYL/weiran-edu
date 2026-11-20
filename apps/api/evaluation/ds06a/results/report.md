# DS-06A 编程错误模式可复现评测结果

> 本报告由 `python -m evaluation.ds06a.run_eval` 生成。数据全部为独立合成样本；
> 未执行提交代码、未修改数据库、未调用真实 Qwen。耗时是当前环境实测，Token 与真实费用为 N/A。

## 总体结果

| 能力 | 基础统计 | 现象发现 | 证据定位 | 特征提取 | 关联/卡片 | 无依据根因断言 | 正确弃答 | 来源一致 | 最低分布现象准确率 | 总耗时 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 100.0% | 100.0% | 100.0% | N/A | N/A | 0 | 100.0% | 100.0% | 100.0% | 1.071 |
| B | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0 | 100.0% | 100.0% | 100.0% | 16.613 |
| C | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0 | 100.0% | 100.0% | 100.0% | 156.000 |

重复运行语义结果一致：**True**（repeat=2；不含耗时和随机 Fact ID）。

## 分分布现象发现准确率

| 分布 | A | B | C |
|---|---:|---:|---:|
| balanced_10x1_50pct_failure | 100.0% | 100.0% | 100.0% |
| confounded_10x1_50pct_failure | 100.0% | 100.0% | 100.0% |
| mixed_incomplete_evidence_10x1 | 100.0% | 100.0% | 100.0% |
| no_test_points_10x1 | 100.0% | 100.0% | 100.0% |
| privacy_limited_3x1 | 100.0% | 100.0% | N/A |
| source_integrity_rejection | 100.0% | 100.0% | N/A |
| source_missing_rejection | 100.0% | 100.0% | N/A |
| two_failure_clusters_10x1 | 100.0% | 100.0% | 100.0% |
| two_manual_defect_types_10x1 | 100.0% | 100.0% | 100.0% |
| two_problems_10_and_15_rows | 100.0% | 100.0% | 100.0% |

## 逐案状态与指标

| 案例 | 系统 | 状态 | 现象 | 证据 | 弃答 | 来源 | 根因断言 | 耗时 ms |
|---|---|---|---:|---:|---:|---:|---:|---:|
| association_not_causation | A | ok | 100.0% | 2/2 | 1/1 | 15/15 | 0 | 0.127 |
| association_not_causation | B | ok | 100.0% | 4/4 | 1/1 | 15/15 | 0 | 1.802 |
| association_not_causation | C | ok | 100.0% | 4/4 | 2/2 | 92/92 | 0 | 14.433 |
| duplicate_submission | A | rejected/duplicate_submission | N/A | N/A | 1/1 | N/A | 0 | 0.006 |
| duplicate_submission | B | rejected/duplicate_submission | N/A | N/A | 1/1 | N/A | 0 | 0.007 |
| duplicate_submission | C | rejected/duplicate_submission | N/A | N/A | 1/1 | 1/1 | 0 | 0.007 |
| incomplete_code | A | ok | 100.0% | 2/2 | N/A | 15/15 | 0 | 0.107 |
| incomplete_code | B | ok | 100.0% | 2/2 | 6/6 | 15/15 | 0 | 1.389 |
| incomplete_code | C | ok | 100.0% | 2/2 | 10/10 | 47/47 | 0 | 11.236 |
| insufficient_test_evidence | A | ok | 100.0% | 2/2 | 1/1 | 15/15 | 0 | 0.056 |
| insufficient_test_evidence | B | ok | 100.0% | 2/2 | 1/1 | 15/15 | 0 | 0.756 |
| insufficient_test_evidence | C | ok | 100.0% | 2/2 | 5/5 | 47/47 | 0 | 10.263 |
| manual_boundary_index_input | A | ok | 100.0% | 4/4 | 1/1 | 20/20 | 0 | 0.102 |
| manual_boundary_index_input | B | ok | 100.0% | 4/4 | 1/1 | 20/20 | 0 | 1.706 |
| manual_boundary_index_input | C | ok | 100.0% | 4/4 | 3/3 | 142/142 | 0 | 36.813 |
| missing_witness | A | ok | 100.0% | 2/2 | 1/1 | 15/15 | 0 | 0.092 |
| missing_witness | B | ok | 100.0% | 4/4 | 1/1 | 15/15 | 0 | 1.563 |
| missing_witness | C | rejected/source_scope_mismatch | N/A | N/A | 1/1 | 1/1 | 0 | 2.984 |
| same_structure_different_points | A | ok | 100.0% | 4/4 | 1/1 | 30/30 | 0 | 0.157 |
| same_structure_different_points | B | ok | 100.0% | 4/4 | 1/1 | 30/30 | 0 | 1.997 |
| same_structure_different_points | C | ok | 100.0% | 4/4 | 3/3 | 182/182 | 0 | 26.939 |
| same_verdict_different_features | A | ok | 100.0% | 2/2 | 1/1 | 15/15 | 0 | 0.090 |
| same_verdict_different_features | B | ok | 100.0% | 4/4 | 1/1 | 15/15 | 0 | 1.880 |
| same_verdict_different_features | C | ok | 100.0% | 4/4 | 2/2 | 92/92 | 0 | 14.659 |
| small_sample_privacy | A | ok | 100.0% | 2/2 | 1/1 | 5/5 | 0 | 0.044 |
| small_sample_privacy | B | ok | 100.0% | 2/2 | 1/1 | 5/5 | 0 | 0.633 |
| small_sample_privacy | C | ok | N/A | N/A | 2/2 | 1/1 | 0 | 6.132 |
| varied_distribution | A | ok | 100.0% | 4/4 | 1/1 | 30/30 | 0 | 0.197 |
| varied_distribution | B | ok | 100.0% | 6/6 | 2/2 | 30/30 | 0 | 3.304 |
| varied_distribution | C | ok | 100.0% | 6/6 | 4/4 | 183/183 | 0 | 29.661 |
| witness_mismatch | A | ok | 100.0% | 2/2 | 1/1 | 15/15 | 0 | 0.092 |
| witness_mismatch | B | ok | 100.0% | 4/4 | 1/1 | 15/15 | 0 | 1.577 |
| witness_mismatch | C | rejected/source_scope_mismatch | N/A | N/A | 1/1 | 1/1 | 0 | 2.873 |

## 解释边界

- A 是本评测独立实现的确定性统计，不冒充 B-01 数据库接口；B/C 复用同一 A 作为组合基线。
- B 的特征和关联来自真实 DS-03 `analyze` / `c01_feature_associations`。
- C 的多步卡片与证据来自真实 C-01 `investigate_programming` / `executor.resolve`；离线选择器只根据实际工具输出行动。
- 统计准确率高不等于根因诊断正确；本基准专门检查关联不被提升为因果。
- C 的隐私门限会抑制小单元；这类案例计入正确弃答，不计入其可发现现象分母。
- 未验证真实 Qwen 推理质量、Token、费用、正式 Provider、B-03 持久化及网页端到端。
