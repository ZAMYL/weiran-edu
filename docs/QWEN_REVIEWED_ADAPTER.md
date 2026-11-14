# Reviewed Qwen AI adapter

Entry: `prototypes.qwen_execution.runtime.run_reviewed_profiles`. No teacher route is mounted.
Uses the existing ProfilePlanner → QwenAdaptivePlanner → NativeDialogue, `_run_session`,
ProfileExecutor, project_selected and CampaignBudget journal. No second orchestration loop.
Legacy `deny_live`, TaskStore, durable task API and frontend stay unchanged.

## Approval and execution

Run from repo root with the locked API Python environment and `PYTHONPATH=apps/api`:

```powershell
$env:PYTHONPATH = 'apps/api'
uv run --project apps/api --locked python -m prototypes.qwen_execution.cli
```

Dry-run loads no credentials and makes no HTTP request. It prints the source, code and
price fingerprints needed for approval. A trusted operator must separately supply a
ReviewedApproval JSON outside Git, with the inherited SyntheticLiveAuthorization fields
plus `independent_review_reference` and `reviewed_code_sha256`. An approval reference is
an operator assertion, not a cryptographic signature or a substitute for human review.
Do not expose this constructor or approval file to a browser/client.

Approval binds campaign ID, exact source fingerprints, model, endpoint SHA256, price
fingerprint, expiration, at most 6 requests / 800 output tokens, token and CNY caps.
Use a fresh campaign ID for each authorized run. The campaign journal is owned by the
server, not by the caller: it lives under `WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT` (default
`%LOCALAPPDATA%\weiran-edu\campaign-ledgers` on Windows, `$XDG_DATA_HOME` or
`~/.local/share` elsewhere) and no CLI flag, working directory or report path can move it.
A relative root is rejected, and one human approval reference may fund only one campaign.
The conservative six-request upper bound at current full-context reservation is
4.7736384 CNY (5,955,648 tokens); approve 4.78 CNY only if acceptable. Actual list-price
cost is computed from returned usage and is not an invoice. Unknown usage retains the
reservation and blocks further requests. No retry or automatic resume.

After independent review AND explicit fee approval, operator execution is:

```powershell
# DASHSCOPE_API_KEY and DASHSCOPE_BASE_URL are server environment variables only.
# WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT pins the ledger; keep it constant across runs.
uv run --project apps/api --locked python -m prototypes.qwen_execution.cli `
  --execute --approval <external-approval.json>
```

Replacing/deleting the approval revokes it; access is checked before each reservation,
after responses and before publication. This is an operator trust boundary, not tenant auth.
No real request was authorized as part of implementation. Mock tests remain labeled
`http_mock`, and cannot produce `qwen_live` evidence.

Official model/price check 2026-11-12:
https://help.aliyun.com/zh/model-studio/qwen3-8-flash
Beijing: input 0.8, cached input 0.1, output 2.7 CNY/million tokens; Function Calling
supported. Account/key access remains unverified until an explicitly approved request.

## Backend integration input contract (not enabled)

Backend owner supplies batch_id, snapshot_id, a trusted current-authorized data accessor,
one task-owned shared budget, current-purpose authorization callback, separately reviewed
model-externalization admission and cancellation signal. Accessor must bind source hash,
actual scores and Witness resolvers to the snapshot; reauthorization is required at every
read, model request and publication. An uploaded JSON or a caller boolean is insufficient.
Current adapter accepts only exact AdmittedSynthetic, so DB/teacher sources fail closed.
Connecting DatabaseSource requires an independently reviewed admission implementation by
the backend owner; it must not pretend that the synthetic permit grants teacher consent.

Output carries status/stop_reason, model choices, actual tool ledger, verified facts,
profile cards, source replay status, origin and usage/cost. Profiles retain the existing
card shape and only the primary outcome is an Agent Fact. Frontend/durable result DTOs
currently require `http_mock`/`real_qwen=not_run`: live results must NOT be relabeled to
pass them. Backend must add an explicitly reviewed provenance union before connecting
this adapter to TaskStore or exposing live results to the current frontend.

## Local acceptance (2026-11-12)

Python 3.12.10, pytest 9.1.1, Windows; reused the existing locked API virtual environment.
`python -m pytest -q apps/api/tests/ai_edu/test_reviewed_qwen_adapter.py
apps/api/tests/adaptive_assessment/test_qwen_live.py
apps/api/tests/research_v2/test_capability_profiles.py`: **77 passed in 536.50s**.
Includes actual offline multi-round native HTTP handling, Fact/Witness resolution,
revocation after response, source conflict, cost refusal, campaign non-reset and old
profile/privacy/oracle regressions. New-path tests use MockTransport and prove no real
Qwen effectiveness. Ruff check and format check passed for all eight changed Python files.
CLI dry-run passed; sanitized output is in `evals/qwen_reviewed_adapter_dry_run.json`.
Full repository pytest, GitHub CI, independent code review, paid request, model account
availability and teacher frontend live-result decoding were **not run / not approved**.
