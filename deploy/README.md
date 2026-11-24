# 未然 EDU 单机生产试点部署

这是 **单台 Linux 主机** 的部署包，不是多租户高可用方案。当前代码路径：

```text
Caddy (TLS)
  → Next.js web:3000（/api/edu 同源代理）
  → FastAPI api:8000（仅内网）
  → worker（独立进程，领取 queued 任务）
  → DashScope
```

## 前提

- Linux 主机（2C4G 起步，持久磁盘）
- 域名解析到主机，80/443 可达
- Docker Engine + Compose v2
- 已获批准的测评数据来源、`RealDataAdmission` 文件
- 已获批准的单次 `DatabaseLiveApproval` 文件（每个 campaign 一份）
- 已获批准的单次 `ProgrammingLiveApproval` 文件（每个正式编程调查一份）
- 服务端 DashScope Key

## 目录

```text
deploy/
  .env.example
  Dockerfile.api
  Dockerfile.web
  docker-compose.yml
  Caddyfile
  secrets/            # 不提交：审批文件
  data/               # 不提交：SQLite + AI state
  ledger/             # 不提交：campaign journal
```

## 1. 准备配置

```bash
cd deploy
cp .env.example .env
chmod 600 .env
# 编辑 .env：DOMAIN、DASHSCOPE_API_KEY
```

`.env` 不能提交；Key 只放服务端。

## 2. 准备三个仓库外审批文件

### 真实数据准入（长期）

`secrets/real-data-approval.json`：

```json
{
  "purpose": "real_teacher_data",
  "human_approval_reference": "OWNER-REAL-20261117",
  "independent_review_reference": "REVIEW-REAL-20261117",
  "reviewed_code_sha256": "<最终提交的 code_fingerprint>",
  "valid_until": "2026-12-31",
  "sources": [
    {
      "source_sha256": "<真实批次原文件 SHA-256>",
      "valid_until": "2026-12-31",
      "human_approval_reference": "OWNER-REAL-20261117"
    }
  ]
}
```

- 这个文件决定哪些真实批次可以进入 AI 链路；未列出的批次返回 `AI_REAL_DATA_NOT_APPROVED`。
- `reviewed_code_sha256` 由 `prototypes.qwen_execution.code_fingerprint()` 生成；代码变更后必须重审。
- 正式应用启动时核对该审批的代码指纹和有效期。每个任务的 `LiveAdmission` 独立核对单次审批。

### 单次 Live 任务（每个 campaign 一份）

`secrets/qwen-live-approval.json` 使用 `DatabaseLiveApproval` 结构（`purpose=database_live`、`source_fingerprints` 绑定 exact source SHA、`campaign_id`、模型/端点/价格/费用上限/有效期）。**一个 campaign 只能使用一次**；换任务要换文件。

### 正式编程调查（每个测评一份）

`secrets/programming-live-approval.json` 使用 `ProgrammingLiveApproval` 结构，字段与样例见
[`programming-live-approval.example.json`](programming-live-approval.example.json)，详细语义见
[`docs/LIVE02_PROGRAMMING.md`](../docs/LIVE02_PROGRAMMING.md)。

- `source_fingerprints` 必须写 `{assessment_id: source_snapshot_id}`，只允许精确匹配；不在表里的测评返回 `AI_LIVE_SOURCE_NOT_APPROVED`。
- **一条 `human_approval_reference` 只能资助一个 campaign**，换测评或在同一测评重跑都要新文件；复用会报 `AI_CAMPAIGN_ALREADY_USED`。
- `price_fingerprint` 与 `valid_until` 取当前审核价快照；`REVIEWED_PRICE_20261119` 有效至 `2026-11-26`，过期必须重新做价格审查并生成新指纹。
- `reviewed_code_sha256` 与 `prototypes.qwen_execution.code_fingerprint()` 一致；只改 `.md`、`deploy/`、`.github/`、`apps/api/tests/` 不影响它，改 `apps/api/app/**` 或 `prototypes/**` 的 `.py` 会失效。
- 该文件同时挂给 `api` 与 `programming_worker` 两个容器；`web` 容器不挂。

## 3. 启动

首次部署先初始化 SQLite，再在 Worker 的配置环境中运行只读自检。启动顺序和获批后的一次合成验收见 [DS-LIVE 联网前验收](QWEN_LIVE_PREFLIGHT.md)。

```bash
docker compose build api
docker compose run --rm --no-deps api uv run --project apps/api --locked python -m app.edu init
docker compose up -d --build
docker compose ps
docker compose logs -f api worker programming_worker
```

Caddy 自动申请 HTTPS。只暴露 80/443；FastAPI 不映射公网端口。

`app.production:create_production_app` 和 `app.main:create_app` 共用同一接线：`WEIRAN_EDU_ENV=production` 且 `WEIRAN_EDU_REAL_DATA_APPROVAL` 存在时，正式 app 直接安装真实 `TaskService` 路由；未配置审批文件时才挂 503 骨架。

## 4. 发放 database_live 授权

```bash
# 操作员先经受控渠道把令牌注入本地环境，再按变量名传入。
docker compose exec -e WEIRAN_EDU_GRANT_TOKEN api \
  python -m app.grant_live --batch <batch_id> --snapshot <snapshot_id>
```

Token 通过容器环境传入。禁止将值写入命令历史或仓库。

## 5. 提交任务

教师页面提交 AI 任务后：

- API 只创建 `queued` 任务并入队，不执行模型；
- worker 领取任务，使用 `DatabaseLiveBudget` + `LiveAdmission` 发出请求；
- 结果/评分依据/报告走现有 HTTP API；
- 同一 `campaign_id` 不能重跑，失败不自动退款。

### 正式编程调查（LIVE-02）

- 入口是 `POST /api/v1/edu/assessments/{assessment_id}/ai-tasks`；`api` 只入队，不执行模型。
- `programming_worker` 独立进程用条件 `UPDATE` 认领 `queued` 行，一个任务只会被一个进程认领。
- 结果、请求数、工具轨迹、来源摘要写入同一 SQLite；重启后用 `GET .../ai-tasks/{task_id}` 仍可查。
- 失败、取消、撤权、超时一律清空结果，并把 `real_qwen` 标为 `failed` 或 `not_run`，不会出现“没跑完但显示成功”。

## 6. 必须遵守的边界

- **单实例、单 worker**：SQLite 和本地账本不是多实例并发方案。LIVE-02 的
  `programming_worker` 是同类任务的第二个进程，两者各只允许一个实例，不能再横向扩。
- **真实数据只外发聚合视图**：不发送姓名、逐人评分、原始答卷、评语、Rubric 正文。
- **审批文件在仓库外**；`data/`、`ledger/`、`secrets/` 不提交。
- **API 启动是 fail-closed**：`WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL` 存在但过期、指纹不符或缺 Key 时，`api` 直接起不来。计划内换审批要同时改文件和环境变量；只想临时停用编程调查，就去掉这个变量（任务会稳定返回 409，其他功能不受影响）。
- **编程 worker 也必须是单实例**：两个进程同时写同一 SQLite 文件会争锁（`busy_timeout=3s`）。
- **没有 Key 时** API/worker 启动不读 Key；任务失败关闭，不回退 Mock。
- **没有服务器时** 本目录只是待部署包，不能宣称已上线。

## 当前未验证

- Docker 构建/Compose 在本机未实际运行（本机无 Docker）。
- `programming_worker` 服务只在 GitHub Actions 的临时 runner 上跑通过，没有在 Docker Compose 中实跑。
- 真实数据运行仍需服务器、真实审批、一次获批的付费执行。
- 多实例、队列高可用、数据库迁移到 Postgres、SSO、备份/监控仍需后续工作。
