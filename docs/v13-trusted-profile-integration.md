# V1.3-P1 trusted database profile integration work record

## Isolation and review dependency

- Approved #72: `757bfd86cdd890e47d9e91fdc52c0742a39c2795`.
- Approved #79: `d5c3270d644be5928ae08b03f758708a3ef72c42` (merge parents
  `a16308d4ceb1d86abefe89ce594ad5c1b67676de` and approved #78 `75332bb16e409eb377f30bb97f83403d4a800408`).
- #72 is an ancestor of #79. #79 contains the old #76 baseline `dfce7cd8d0fefb5c722e77d0093b9ec7d889ee94`.
- #76 fix `821a1452e2478f26e681d4d38ace227f7ed745e3` is **NOT independently approved**
  at the initial check. It is selectively cherry-picked as local dependency commit
  `9dfa7b07be8d150e8d11104f760e260305cf462f` for isolated validation only.
- Final dependency recheck: Peng-Kai789 independently **APPROVED** exact #76 SHA
  `821a1452e2478f26e681d4d38ace227f7ed745e3` at `2026-11-08T13:16:56Z`.
  The review prerequisite is now satisfied; publication may proceed after local validation.
- Local worktree: `D:/AI/weiran-edu-v13-profile-integration`, branch
  `codex/edu-v13-trusted-profile-integration`. No original PR is modified. Do not push
  a formal integration candidate or open an integration PR while #76 remains unapproved.

## Code inspection before implementation

1. **Database binding already implemented:** `DatabaseProvider` registers only server-held
   workbook hashes using the actual formal parser, retains original coordinate/score lexemes,
   checks current owner/snapshot, raw XLSX SHA, rubric/evaluation joins and every source cell.
   It builds real anonymous participants, observations and score/row Witnesses. The assessment
   snapshot remains the database snapshot; the canonical source manifest hash is a distinct
   internal analysis identity. `AuthorizedEnvelope` repeats purpose checks after capture.
   The host remains test-only and accepts 5–100 records. A five-column numeric matrix is not
   registration or protocol evidence.
2. **Profile gap:** `dataset_from_source` accepts exact `AdmittedSynthetic` plus an offline
   recipe allowlist. `ProfileBook` builds and replays candidates against its source fingerprint;
   it has no trusted database contract. The adapter must validate an exact `DatabaseSource`,
   exact server Provider/envelope family, confirmed five-dimension semantics, scale and source
   binding. It must not call `admit` for database data or treat arbitrary Envelopes as trusted.
3. **Task/budget capability:** #72 already owns the only SQLite task/event/grant tables and
   exclusive campaign journal. `Invocation.open_budget` supplies `DurableBudget`; executor
   `durable_audit` charges the statistical seed and each subsequent attempt. `TaskStore.finish`
   atomically commits result/proof, while failures preserve counters and disable reuse. The
   narrow extension is an overridable existing-host execution hook, a reusable profile-session
   body receiving that same budget, and an optional durable-only result projection. Budget
   exhaustion will fail closed, with no partial profiles published in this stage.
4. **Restart/disclosure gap:** profile `teacher_view` stages group and five-dimension bucket
   sets but the offline caller does not commit them into #72 SQLite disclosure history.
   Durable profile completion must commit the combined history atomically with the result,
   preserve a separately replayable tool proof, and bind profile/explanation/calculation rules.
   GET/restart must rebuild candidates, replay actual tool queries/Facts/Witnesses, recompute
   permitted profiles and compare both projection and combined disclosure. It cannot trust
   stored JSON or reset published partitions. No new budget is opened during readback.

## Minimal implementation boundaries

- Add an explicit trusted database-to-profile source contract; keep the offline allowlist.
- Reuse ProfileExecutor, ProfilePlanner, the existing session/seed and deterministic profile
  engine; factor session ownership so a database task never creates a second budget.
- Extend only the existing synthetic durable result/JSON proof and atomic finish transaction;
  preserve old non-profile response shapes and all ordinary group/quality/relation behavior.
- Expose original assessment snapshot and source fingerprint separately. Profile IDs are not
  Fact IDs or evidence IDs. Do not advertise a five-dimensional HTTP drilldown route.
- Keep frontend, formal protocol/statistics, frozen Oracles and Live ban unchanged.

## Existing protocol limitation found during inspection

Formal `current_template.v1` commit and `AnalysisReader._load` require five valid scores per
student (100–500 integer hundredths). Missing/invalid scores are retained as preview issues
and prevent commit; they cannot currently occur in a valid committed snapshot. This stage
does not change that protocol/schema or impute values. Missing-source test cases must be
explicitly rejected rather than fabricated. Thirteen-question analysis stays on its original
per-item path; it is not coerced into a five-dimensional profile source.

## Execution evidence

### Backend contract and publication

The existing test-only durable Task API, with `strategy=adaptive` and an unscoped controlled
priority question (for example `有哪些值得关注的问题？`), now runs the profile session. Scoped
priority, group, relation, quality, explanation and fixed-strategy tasks retain their existing
execution path. No route, client budget, upload endpoint, table, migration or dependency is added.
The ordinary application still does not mount this synthetic host.

`DatabaseProfileSource` wraps the actual validated database source; it does not call `admit`,
register a database as a recipe, or accept arbitrary Envelopes. The formal competency codes,
item identities, ordinal scale, batch, assessment snapshot and server source binding are checked.
`ProfileBook` and `ProfileExecutor` continue to use the shared deterministic scoring Kernel,
discovery, Fact, Witness, explanations and cumulative disclosure gate.

`run_profile_session` receives the **same** `DurableBudget` opened by #72. It neither creates
nor closes a campaign. The seed and subsequent tool attempts use the existing SQLite audit;
the model reservation journal remains exclusive and fsync-before-request. Completion atomically
stores the existing result/proof plus combined tool/profile disclosure sets in the existing row.
Budget exhaustion publishes **no** partial results and leaves the task failed, with its consumed
budget and conservative history intact. It does not gain a new partial-completion state.

The optional `profile_projection` stores original assessment snapshot separately from internal
source fingerprint, profile IDs separately from primary Fact IDs and real evidence IDs, verified
conditions/counts/five-dimensional statistics, rule-generated teacher explanation versions,
stop/coverage information and measured audit data. A fresh GET reconstructs source, candidates,
ordered tool proof, primary Facts/Witnesses, permitted profiles and combined disclosure history.
Projection JSON and its checksums alone cannot authorize a result. Readback spends no new model
or tool allowance. Runtime policy/rule changes fail closed instead of reinterpreting old disclosure.

Existing primary Fact `records/query` links continue to return actual scoring evidence. Full
five-dimensional HTTP drilldown is **not implemented**: offline-only `source_queries` are removed
from this database projection, and `dimension_evidence_http=not_implemented` is explicit.

### Reproduction

The new `apps/api/tests/durable_teacher_agent/test_database_profiles.py` generates actual XLSX
using the existing `scripts.edu_demo_fixtures.make_fixture` writer and approved profile fixture
matrices; it replaces only formal score cells. Each acceptance task uses real FastAPI preview,
confirmation, temporary SQLite, server purpose authorization, the original Task POST and GET.
There is no pseudo-upload or imported offline answer JSON. `QwenSettings.from_env` is forbidden
by these tests; only the existing HTTP Mock transport is used.

From `apps/api`, with the locked development environment installed:

```powershell
uv run --locked --no-sync pytest tests/durable_teacher_agent/test_database_profiles.py -q -s
```

The suite prints `DATABASE_PROFILE_EVIDENCE` for each actual task, including original snapshot,
source fingerprint, returned profiles, budgets, search counts, replay counts and timings. No
temporary workbooks, SQLite databases, credential values or student records are committed.

### Actual results

New regression coverage: **18 distinct tests passed across targeted runs** (16 actual HTTP/SQLite
tests and two explicit thirteen-question adapter rejection tests). Existing offline profile tests
are reported separately and are not counted as new HTTP/SQLite acceptance.

Unchanged original targeted regressions: **246 passed**, including the real 53-person task,
Fact/evidence GET, formal recomputation/review/report flow, persistent lifecycle, full goal
admission, bridge provenance, thirteen-item statistics, profile/explanation boundaries, model/tool
budgets and Live prohibition. The 53-person ordinary group result retains target/remainder 26/27,
exact difference -4, real evidence replay and report behavior. Existing assertions were not deleted,
skipped or relaxed. API Ruff and format (228 files), and prototype Ruff/format (24 files) pass.

| Actual formal-import task | HTTP / task state | Returned profiles | Model / tool attempts |
| --- | --- | --- | --- |
| balanced 72 | POST 202, GET 200 / completed | Two discovered 24-person groups, 48-person remainders | 3 / 3, including seed |
| no-pattern 81 | POST 202, GET 200 / completed | Zero, `no_eligible_profiles` | 1 / 1 |
| overlap 96, runtime k=25 | POST 202, GET 200 / completed | One permitted 48-person group; unsafe histogram buckets suppressed | 2 / 2 |
| balanced 72, tool cap=2 | POST 202, GET 200 / failed | No result published; `AI_PROFILE_TOOL_BUDGET_EXHAUSTED` | 2 / 2 |
| balanced 72, model cap=1 | POST 202, GET 200 / failed | No result published; `AI_PROFILE_MODEL_BUDGET_EXHAUSTED` | 1 / 2 |

The 72-person test checks all **360 committed scoring cells** against the input rule matrix,
reconstructs each group's members from actual SQLite conditions, and independently checks every
dimension's target/remainder mean and complete score distribution using `Fraction`. Actual groups:

| Condition | n / remainder | Target five means (algorithms, implementation, modeling, efficiency, strategy) | Remainder means |
| --- | --- | --- | --- |
| algorithm score <= 2.25 | 24 / 48 | 2, 4, 3, 3, 4 | 3.5, 2.5, 3, 3, 2.5 |
| algorithm score >= 3.75 | 24 / 48 | 4, 2, 4, 3, 2 | 2.5, 3.5, 2.5, 3, 3.5 |

These are independently checked fixture expectations in tests, never a program branch on 72 or
a fixed output count. The original batch/snapshot and score evidence remain actual database IDs:

- Task: `de6975a5-254b-41f1-93cc-637ae23607cf`.
- Batch: `d59378b3-585e-49b6-b4dd-72976ab9e8b6`.
- Assessment snapshot: `6a7495dd-d163-49e6-85cc-4e5ab813125c`.
- Source manifest SHA: `306b9b784e8df1c42c5177142374813ef9fc5658cc2c064ae3a477c45a3c658b`.
- Primary Facts: `profiles-5c3102931206-f002` and `profiles-5c3102931206-f003` (both actual
  `compare_student_groups` Facts; `f001` is the charged statistical seed).
- The main Fact's actual target Witness IDs/membership are matched against SQLite evidence IDs;
  the original records HTTP endpoint and two actual evidence GETs also succeed. Five-dimensional
  auxiliary statistics are deterministic verified summaries, **not** five newly issued Agent Facts.

Read-only extraction from this actual temporary SQLite task confirms **one task / one campaign**
`durable-847c4d5501c50f888b5eda1dfce177708dde377a`, three settled model reservations, three
`tool_attempt` and three `tool_verified` events, one `result_committed` event, and **36 combined
disclosure entries**, including 21 original tool-history entries. The eight `model_budget` audit
events include initialization/reservation/settlement; they are not eight requests. Source binding
is the single authorized batch fingerprint above. Fresh TaskStore/TaskService replay and duplicate
HTTP POST preserve exact result, budget JSON, proof JSON, tool count and disclosure JSON; only one
JSONL campaign journal exists. No planner or charge is invoked by readback.

The actual readback rejects old snapshots/cross-batch requests (409), other teachers (404), actual
score corruption (503), recomputed-checksum profile-mean tampering (503), removed combined
disclosure entries (503), obsolete explanation policy (503), and malformed saved projection (503).
The corruption test disables/restores an immutable-score trigger **only inside its temporary
synthetic database** to exercise recovery beyond the ordinary database mutation guard. Production
triggers and migration files are unchanged. A purpose revocation immediately after actual capture
returns 403 `AI_PURPOSE_REVOKED`, retaining budget/history. In-flight cancellation, timeout and
revocation preserve the charged seed and publish no result; repeated requests cannot reset a
campaign. Runtime k=25 history is independently partitioned into atoms: no nonempty atom is below
k, and another task on the same grant is rejected.

An unregistered but otherwise valid 53-person formal workbook is denied synthetic-purpose
authorization (403), missing-score commit returns 409 `IMPORT_NOT_CONFIRMABLE`, and invalid
numeric-only/non-protocol workbooks cannot reach an AI task or campaign. The two thirteen-question
sources are explicitly rejected by the profile contract while their original statistics remain
covered by unchanged per-item regressions.

Measured seconds from the successful real HTTP tasks on this Windows machine:

| Scope | 72 people | 81 people |
| --- | ---: | ---: |
| Candidate search (50 attempts each) | 1.9734 | 1.2887 |
| Model HTTP Mock | 0.0040 | 0.0010 |
| Tool execution | 1.2990 | 0.3056 |
| Fact/source proof replay | 44.3523 | 3.0462 |
| Profile-source replay | 4.9196 | 0 |
| Profile session total | 58.4289 | 6.9676 |
| Task POST + verified GET wall time | 97.2891 | 14.8764 |

The 72-person projection records 61 actual query verification replays and ten profile-source
replays. These timers have nested scopes and **must not be added**. Session total includes candidate
search/session/profile projection, while the end-to-end measurement additionally includes the
parent host's final Fact/Witness projection, durable commit and verified GET. Readback timing is
not falsely reported as model inference latency. Real remote requests, tokens and fees: not run.

### Changed files and remaining scope

- `prototypes/capability_profiles/database.py`: explicit trusted database contract.
- `prototypes/capability_profiles/engine.py`: admit that contract; retain offline allowlist and
  scoring/discovery rules; record actual source replay time.
- `prototypes/capability_profiles/agent.py`: caller-owned reusable session, shared durable audit,
  staged disclosure return and actual timing; existing offline runner stays a wrapper.
- `prototypes/teacher_agent_bridge/api.py`: narrow execution hook; original default path unchanged.
- `prototypes/durable_teacher_agent/models.py`: optional durable profile result projection.
- `prototypes/durable_teacher_agent/profiles.py`: execution adaptation and fresh proof/disclosure
  reconstruction, identity separation, primary Fact/Witness verification and fail-closed results.
- `prototypes/durable_teacher_agent/service.py`: priority task dispatch and one authoritative
  verification pass per read; existing task, authorization, budget and failure ownership retained.
- `prototypes/durable_teacher_agent/store.py`: atomic combined disclosure commit in existing row.
- `apps/api/tests/durable_teacher_agent/test_database_profiles.py`: the 18 new regressions above.
- This work record. The four #76 admission-fix files are a separately reviewed dependency patch,
  not a reimplementation or modification of the original PR.

Passed functionality is limited to the authorized **synthetic test host**. Real model/teacher
data/production AI remain disabled. Partial-profile publication on exhausted budgets, complete
five-dimensional evidence HTTP drilldown, frontend drawers, and broader production rollout remain
next-stage work. No frontend, formal scoring/import protocol, frozen Oracle, migration, dependency,
CI configuration or original PR branch is modified. No merge or deployment is performed.

Final original-regression counts, local source SHA and fresh CI are recorded in the integration
PR against the final combined commit; earlier CI is not used as evidence for this integration.

Two issues were caught during development without weakening existing assertions: the adapter's
trace needed the original host's required keys, and the root empty-result stop code must remain
`no_supported_findings` while the optional profile projection says `no_eligible_profiles`.
One run also experienced a long wall-clock stall (72-person task exceeded its original deadline
and failed closed). That run is excluded from successful acceptance; timeouts were not extended.
