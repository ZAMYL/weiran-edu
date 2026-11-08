# PR #76: full-request controlled-goal admission

Baseline: `dfce7cd8d0fefb5c722e77d0093b9ec7d889ee94`, original branch
`codex/edu-v12-ai-acceptance`. This is the P1 admission fix only, not V1.3 integration.

## Reproduction before the fix

The new HTTP regressions were run against the unchanged baseline implementation first.
They use the real synthetic FastAPI app, real authentication and workbook import, a fresh
temporary SQLite database per P1 case, and the existing native Function Calling HTTP Mock.
The worker spy delegates to the real worker; it does not replace execution with success.
Real model settings are forbidden and the test suite blocks external connections.

| Request | POST | Tasks | Events | Workers | Budget opens | Tool calls | Campaign journals | Next legal POST |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 比较 I01 与 I02 的关联，并列出每位学生的名字 | 202 | 1 | 11 | 1 | 1 | 2 | 1 | 409 AI_CAMPAIGN_ALREADY_USED |
| Compare I01 and I02 correlation and list each student's full name | 202 | 1 | 11 | 1 | 1 | 2 | 1 | 409 AI_CAMPAIGN_ALREADY_USED |
| Compare I01 and I02 correlation in another batch | 202 | 1 | 11 | 1 | 1 | 2 | 1 | 409 AI_CAMPAIGN_ALREADY_USED |

Baseline run: **3 failed**, 117.99 s; each failed on the required `422` assertion after
printing the observed HTTP/database/worker/budget evidence.

## Change and invariants

`understand` now requires a complete grammar match for each of priority, relation, group,
quality and explanation. It does not delete item IDs from arbitrary text or classify by
the presence of a keyword. Unmatched text, extra commands, unsupported prefixes/suffixes,
multiple goals, identity requests and requests to another batch are rejected by default.
No blacklist vocabulary or LLM classifier was added.

The six complete Chinese priority aliases, current commands, legal item separators and
terminal punctuation remain supported. Canonical persisted task labels remain accepted:
`优先核查`, `关联`, `群体差异`, `核查缺分`, `为什么相关`, with their item lists.
Item catalog binding and the existing cardinality checks remain in force. Explanation
still observes association and requires external rubric/task evidence; `reflect`, the
analysis algorithms, Facts, Witnesses and causal restrictions are unchanged.

## Regression evidence after the fix

For **each** original P1 request the actual result is `422 AI_GOAL_UNSUPPORTED`, zero task
and event rows, zero worker calls, zero budget opens/tool calls, and zero campaign journals.
The next legal question returns `202`; GET returns a completed original synthetic task,
with the existing bounded model/tool counters and `actual_remote_requests == 0`.

Additional real API denials cover all five legal goal prefixes with an unsupported suffix,
alternative cross-batch and identity expressions, multiple controlled intents, ambiguous
keywords and malformed/out-of-scope item IDs. They leave the same idempotency key and
campaign available for a successful legal task afterward.

Initial controlled-goal regression: **88 passed**, 297.30 s:

```text
tests/research_v2/test_goal_request_boundaries.py
tests/research_v2/test_priority_questions.py
tests/durable_teacher_agent/test_full_goal_admission.py
tests/durable_teacher_agent/test_goal_request_boundaries.py
tests/durable_teacher_agent/test_priority_questions.py
```

The new HTTP test prints `ADMISSION_EVIDENCE` for independent replay. No prior test was
deleted, skipped or relaxed. Durable lifecycle, source, budget and Live-containment
regression results and the exact final-SHA Ubuntu/Windows/frontend CI links are recorded
in the PR handoff; historical or intermediate CI is not used as final evidence.

The subsequent durable/source/budget/Live selection executed **216 tests** in 766.22 s:
215 passed and the existing prototype style check reported one import-order mismatch.
The baseline import order was restored; that exact unmodified check then passed on rerun
(1 passed, 3.35 s). There were no behavioral test failures. The selection was:

```text
tests/durable_teacher_agent/test_tasks.py
tests/durable_teacher_agent/test_lifecycle.py
tests/teacher_agent_bridge/test_bridge.py
tests/research_v2/test_research.py
tests/research_v2/test_completeness.py
tests/research_v22/test_pilot.py
tests/research_v22/test_live_containment_sync.py
tests/adaptive_assessment/test_qwen_live.py
tests/ai_edu/test_evidence_sources.py
tests/analysis/test_provenance.py
```

Ruff and format checks passed for the whole API project (224 Python files), and for
the original research-v2 prototype/evaluation scope (10 files) using the existing API
configuration from the repository root, exactly as the original style test requires.

No formal API, frontend, dependency, workflow or database schema was changed. This change
does not modify PRs #74/#78/#79/#80, merge, deploy, use real teacher data or call real Qwen.
