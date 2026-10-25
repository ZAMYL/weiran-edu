# Canonical assessment 合成设计验证

仅验证任务 #8 的通用项目保留、准备度和可选资料建议；完全独立于正式应用。相关设计：[通用模型](../CANONICAL_ASSESSMENT_MODEL.md)、[准备度](../ANALYSIS_READINESS_DESIGN.md)。

产品展示按 [PR #23 负责人反馈](https://github.com/ZAMYL/weiran-edu/pull/23#issuecomment-6392744109) 采用“分析优先、资料建议次之”，具体见准备度设计第 4 节。原型的 `suggested_resources`、`profile` 和 `missing` 是内部信息，不是教师端必显清单；教师首屏展示实际项目与可用分析，可选建议默认折叠，不按五维显示覆盖百分比或完成度。三维、八维和十三题的界面文案是未来目标，当前固定模板尚不支持其直接上传。

本次修订不改变原型结构、规则、A–H 合成输出或测试；既有快照一致性测试用于确认保留原结果，不代表展示契约已经在前端实现。

- `model.py`：单 Dataset/Batch 数值子集、动态项目、精确量表、观测和受控证据声明。
- `readiness.py`：按当前用途与逐记录资料生成准备度，不计算正式统计或执行复核。
- `synthetic.py`：A–H 完全合成夹具；`synthetic_outputs.json` 为实际运行输出。
- `test_readiness.py`：A–H、动态人数/项目数、非法值、精度、授权撤销、证据归属和空集合等边界。

从仓库根目录复现（复用后端锁定的 Python 3.12 / pytest / Ruff，无新增依赖）：

```powershell
cd apps/api
uv run --locked ruff check ../../docs/canonical_assessment
uv run --locked ruff format --check ../../docs/canonical_assessment
uv run --locked pytest -q ../../docs/canonical_assessment
cd ../..
apps/api/.venv/Scripts/python.exe -m docs.canonical_assessment.synthetic
```

Linux/macOS 最后一条使用 `apps/api/.venv/bin/python -m docs.canonical_assessment.synthetic`。生成 UTF-8 输出文件可用标准库，避免 PowerShell 不同版本的重定向编码差异：

```powershell
apps/api/.venv/Scripts/python.exe -c 'import json; from pathlib import Path; from docs.canonical_assessment.synthetic import scenarios; from docs.canonical_assessment.readiness import assess; Path("docs/canonical_assessment/synthetic_outputs.json").write_text(json.dumps({k: assess(v).to_dict() for k,v in scenarios().items()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")'
```

默认后端 CI 只收集 `apps/api/tests`，不会自动收集这里的测试；本轮不修改共享 CI。评审应执行上面的显式命令，正式接入前再为设计验证安排独立 CI 检查。

所有 ID 和数值均由夹具构造，证据无正文、定位符或学生身份。`permitted_purposes` 只是合成声明，不是真实身份验证或可提交的客户端授权。真实来源必须由服务端重新检查，不能直接使用该原型处理学生数据。

原型不实现多次作答聚合、多批次可比性、量表测量水平、非数值观测、通用解析/存储/HTTP、真实授权或完整分析引擎；这些是设计边界。参考现有 V1 时只读，不能把新增项目截断后塞入五维枚举。
