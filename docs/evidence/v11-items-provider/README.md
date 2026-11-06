# V1.1 逐题与Provider本轮实际证据

- 被验收的代码提交：`af00cb07ecf6accdc66ceaa818591014658ea4f1`，在原#71分支继续开发。
- [真实TCP HTTP完整请求/响应](http.synthetic.json)：16次请求均符合预期，20名合成参评者、13题/260条原观测，257条合法评分、1缺分、1超范围、1步长不符。十三题原量表及均值/合法分布独立对账通过；只读/撤权、错误快照、来源与重复导入通过。外发用途拒绝，真实Qwen调用0次，无浏览器验收。
- [本轮定向JUnit](focused.junit.xml)：141 passed，48.87秒，1条既有Starlette弃用warning；本轮没有重跑1589项或已有V1.1全量，不把旧结果冒称当前增量CI。
- [文件原字节摘要](SHA256.json)，通过.gitattributes保留原件换行。后续提交仅发布这些证据和文档，不修改验收后的业务代码。

范围：`test_question_xlsx.py`、`test_question_flow.py`、`test_question_contract.py`、`test_question_provider.py`、`test_direct_upload.py`、`test_http.py`、`test_permissions.py`、`test_analysis_api.py`。覆盖实际解析入库、动态项目分布、零有效评分、旧五维兼容、权限和来源，以及Provider新lease累计披露、微小交集拒绝、伪造/撤销/取消lease和远程用途阻断。

复现（仓库根目录、锁定环境已就绪）：

```powershell
uv run --project apps/api --locked python scripts/edu_question_acceptance.py --output <新的合成记录路径.json>
```

脚本仅启动本机回环服务和独立临时SQLite，不读取已有数据库/教师原件，不记录令牌或请求Authorization头，不产生真实模型调用。输出路径必须不存在。参评键是合成匿名键，不推断班级、学校或跨测评身份。
