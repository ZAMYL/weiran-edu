# 合成 AI 联调交接（#72 的单一任务契约）

这是临时本机 HTTP Mock 环境，不是正式教师AI入口。基线 bda5d37，选择性封禁全部 Live；未改 app、apps/web、数据库迁移或任何原 PR。

## 启动

在本分支根目录、现有锁依赖的 Python3.12 环境：

```powershell
$env:PYTHONPATH=".;apps/api"
$env:PYTHONUTF8="1"
python -m prototypes.durable_teacher_agent.serve
```

仅绑定127.0.0.1。输出的是用户临时目录内 connection.private.json 的路径，不输出Token。文件仅供本机开发者读取，含一小时合成教师凭证、API源和已授权的normal/evidence/empty三批次。不要复制入仓库、日志或截图。Ctrl+C/一小时停止后撤销凭证，删除临时DB、账本及连接文件。`--seconds 1`可做启动/清理检查；不会发模型请求。

同一环境一批次只允许一个持久campaign，不因新idempotency_key续额度；重启/结果回读的验证用下面原demo。重建整个临时环境是新合成测评，不是生产预算续期机制。没有任意上传授权接口。

## 只接一套接口

以 connection 中真实batch/snapshot替换路径，不从前端传统计或Fact：

- POST `/api/v1/edu/batches/{batch_id}/ai-investigation-tasks`：`snapshot_id,question,idempotency_key`；202，幂等重试原key。
- GET `.../{task_id}?snapshot_id=...`：真实state/error_code/stop_reason；queued/running时轮询，其余停止。不要动画伪造模型思考。
- POST `.../{task_id}/records/query`：`snapshot_id,fact_id,metric_key,offset,limit`；使用返回的受控source_refs。
- POST `.../{task_id}/cancel?snapshot_id=...`：持久取消，无预算退款。
- POST `.../{task_id}/review-submissions`：`snapshot_id,fact_ids`；服务端重算正式合格发现，仅生成pending。不是直接confirmed。
- 探索使用 Fact 的 `exploration_query`；教师确认/不采纳、expected_version与MD/XLSX下载使用receipt的原正式路径。

准确Schema和示例仍是 `docs/contracts/ai-investigation-tasks.openapi.synthetic.json` 与 `docs/contracts/ai-investigation-tasks.http.synthetic.json`，不创建第二版DTO。结果固定 `execution_origin=http_mock,real_qwen=not_run,production_enabled=false`；模型请求计数是Mock协议请求，actual_tokens/invoice_cost_cny为空。

**林明需要补的接线**：当前web同源代理的GET/POST白名单尚无上述任务路径，会404；仅由林明在开发环境接入准确方法/路径，不开放通配转发。EDU_API_ORIGIN取本机连接文件的源；Bearer只放服务端开发会话配置，不放NEXT_PUBLIC。原代理40秒超时不应等待任务执行，应接受202后轮询。现有页面尚未验收此链路，不能宣称正式页面已能使用AI。

## 原闭环复现

```powershell
python -m prototypes.durable_teacher_agent.demo --output D:/Temp/edu-ai-handoff-http.json
```

原demo真实TCP + 两个独立Uvicorn进程 + 同一临时SQLite：上传→确认→调查→持久读取→依据→pending→教师确认→MD/XLSX重开；并含空发现、缺证、撤权、版本冲突。没有浏览器或外部Qwen调用。正式AI用途授权、动态项目AI准入和真实模型独立审批仍是阻断，不以此代替生产鉴权。
