# Qwen task admission: local AI-side handoff

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

> 保留 #83 当时的交付记录。当前产品基线、#95 账本规则及统一调用入口见
> [QWEN_V131_AI_CORE_HANDOFF.md](QWEN_V131_AI_CORE_HANDOFF.md)。旧测试数量、指纹和授权状态不能替代新版本验收。

## Status and independent review

On 2026-11-13 GitHub PR #82 was open/Draft at
`5552ce5c62b671911a7189ebccef1c593c80c697`, with zero reviews and zero comments.
The implementation author cannot approve their own work as an independent review.
No paid-call approval has been granted in this conversation. Remote calls remain zero;
model availability, real choices, real Token usage and real quality are unverified.

The original #82 SHA remains unchanged on GitHub. This separate local increment only
adds an AI-side binding/preflight contract and a provenance DTO; it does not install a
remote teacher API or change backend TaskStore, routes, auth, frontend, or deny_live.
The existing profile session admits the specific DatabaseExecution adapter; its planner
reuses ProfilePlanner and runs preflight immediately before/after model requests.

## Backend-owned input

`prototypes.qwen_execution.task_contract.DatabaseTaskInput` takes the EXISTING task_id,
batch_id, snapshot_id, authenticated Principal, TaskStore, DatabaseSource, task-owned
CampaignBudget, QwenSettings and server-owned require_externalization callback.
No new TaskStore, budget or executor is constructed. The callback is never deserialized
from HTTP, and a client boolean or synthetic_only flag cannot replace it.

`check_binding()` checks the running task, initiating credentials, batch/snapshot/source
fingerprint, source provider and actor, and identity of budget.store/budget.task_id plus
its campaign/source bindings. This method does not grant remote processing permission.

`preflight()` checks the callback before new score validation, reuses the existing
DatabaseProfileSource validation, then reauthorizes task and remote processing after the
read and checks the shared budget. It refuses a Mock budget even if a test callback returns
normally. It makes no network request and does not claim/reset/consume any campaign.
Backend must call it before model context construction and bind the same check to every
model request and final publication; it must not cache a one-time admission result.

Backend execution hook: `await run_database_profiles(binding, limits=existing_limits)`.
It reuses the single run_profile_session, returns `(raw_result, profile_book, executor)`
for the backend's original proof/persistence path, verifies all facts and batches Witness
replay through the original provider, and reauthorizes before releasing output. It does
not allocate a new budget/store or implement another loop. Current backend remains
blocked at preflight because its budget and persistence support only Mock provenance.

The callback receives only a server-created ExternalizationScope: teacher/task/campaign,
batch/snapshot/source hash, model, endpoint and the fixed purpose
`assessment_aggregate_remote_inference`. It must validate current remote-processing
consent, grant revision/expiry/revocation, exact reviewed code/model/endpoint/price and fee
approval. These local IDs are authorization inputs, NOT model message fields.

Current DatabaseProvider only admits registered synthetic XLSX; current TaskStore grant
purpose is synthetic_mock; DurableBudget and store.budget accept only http_mock. This
increment deliberately does NOT turn those into live authorization. Backend must supply
an independently reviewed live permission/persistence binding to the same store/budget
before live DB execution becomes possible. No real teacher file is opened by these tests.

## Disclosure and output

Use existing safe model-view producers only after admission. Do not serialize source,
actor, dataset/participant objects, raw scores, original answers, comments, evidence IDs,
Witness/locator objects or ExternalizationScope. Uploaded item labels/metadata also need
separate egress review; the old catalog's synthetic_only flag is not provenance proof.
All numerical facts remain computed/replayed by existing deterministic tools.

ExecutionProvenance declares separate origin (qwen_live/http_mock/not_run), task state,
request state, model/tool counts, replay status and explicit failure code. It validates
consistency, NOT the truth of a caller claim. Only backend execution and durable ledger
evidence may populate it. A request attempt is not proof of a successful HTTP response.
Do not relabel live as Mock to satisfy old result DTOs. Backend must update the persisted
result contract/proof/provenance together; frontend decoding remains a separate owner task.

## Separate acceptance tracks

1. #82 synthetic remote: requires independent exact-SHA approval and owner's explicit
   <=4.78 CNY approval. Only profile-balanced-72, <=6 model requests / <=5 tools. NOT RUN.
2. Database admission: tests use synthetic formal XLSX → real FastAPI import → temporary
   SQLite → existing TaskStore/DurableBudget. Tests prove denial/binding/revocation,
   not successful live DB inference. Current Mock budget must remain rejected.
3. Real teacher-file remote processing: consent and backend live wiring unavailable.
   NOT RUN. No combined successful end-to-end claim is made from tracks 1 and 2.

Review #82 admission, source egress projections, exact code fingerprint coverage,
operator-owned approval/reference trust, fixed ledger directory and unknown-usage
reservations, response/schema validation and post-response revocation before approving
any real call. A reference string in an approval JSON does not independently verify a
GitHub review or grant permission by itself.

## Actual local verification

Windows / Python 3.12.10 / pytest 9.1.1, locked API environment, PYTHONUTF8=1:

```powershell
python -m pytest -q `
  apps/api/tests/durable_teacher_agent/test_qwen_task_contract.py `
  apps/api/tests/ai_edu/test_reviewed_qwen_adapter.py `
  apps/api/tests/durable_teacher_agent/test_tasks.py::test_persist_reload_evidence_review_and_report
```

Final result: **19 passed, 1 warning in 183.73s**; warning is the existing Starlette
httpx deprecation. Ruff check, format --check (three changed Python files), and
git diff --check passed. The source/consent rejection test invokes the new execution hook
with HTTP client construction forbidden, proving refusal before any request. A successful
DatabasePlanner live send remains untested: no reviewed backend live budget exists.
Full pytest and GitHub CI were not run for this isolated increment. The earlier 77-test
result belongs to the original #82 baseline, not a new full-suite result for this branch.
Any approval/code fingerprint for #82 must not be reused for this changed source tree.
