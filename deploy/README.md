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
- 已获批准的真实数据来源、`RealDataAdmission` 文件
- 已获批准的单次 `DatabaseLiveApproval` 文件（每个 campaign 一份）
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

## 2. 准备两个仓库外审批文件

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
- 当前 `RealDataAdmission` 只校验来源和日期；代码指纹由每次任务的 `LiveAdmission` 复核。

### 单次 Live 任务（每个 campaign 一份）

`secrets/qwen-live-approval.json` 使用 `DatabaseLiveApproval` 结构（`purpose=database_live`、`source_fingerprints` 绑定 exact source SHA、`campaign_id`、模型/端点/价格/费用上限/有效期）。**一个 campaign 只能使用一次**；换任务要换文件。

## 3. 启动

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f api worker
```

Caddy 自动申请 HTTPS。只暴露 80/443；FastAPI 不映射公网端口。

`app.production:create_production_app` 和 `app.main:create_app` 共用同一接线：`WEIRAN_EDU_ENV=production` 且 `WEIRAN_EDU_REAL_DATA_APPROVAL` 存在时，正式 app 直接安装真实 `TaskService` 路由；未配置审批文件时才挂 503 骨架。

## 4. 发放 database_live 授权

```bash
docker compose exec -e WEIRAN_EDU_GRANT_TOKEN="<teacher token>" api \
  python -m app.grant_live --batch <batch_id> --snapshot <snapshot_id>
```

Token 只通过容器环境传入，不写命令行、不写仓库。

## 5. 提交任务

教师页面提交 AI 任务后：

- API 只创建 `queued` 任务并入队，不执行模型；
- worker 领取任务，使用 `DatabaseLiveBudget` + `LiveAdmission` 发出请求；
- 结果/评分依据/报告走现有 HTTP API；
- 同一 `campaign_id` 不能重跑，失败不自动退款。

## 6. 必须遵守的边界

- **单实例、单 worker**：SQLite 和本地账本不是多实例并发方案。
- **真实数据只外发聚合视图**：不发送姓名、逐人评分、原始答卷、评语、Rubric 正文。
- **审批文件在仓库外**；`data/`、`ledger/`、`secrets/` 不提交。
- **没有 Key 时** API/worker 启动不读 Key；任务失败关闭，不回退 Mock。
- **没有服务器时** 本目录只是待部署包，不能宣称已上线。

## 当前未验证

- Docker 构建/Compose 在本机未实际运行（本机无 Docker）。
- 真实数据运行仍需服务器、真实审批、一次获批的付费执行。
- 多实例、队列高可用、数据库迁移到 Postgres、SSO、备份/监控仍需后续工作。
