# AGENTS.md — AI Gateway 项目

## 项目家风（最高优先级，覆盖其他所有规则）

### 行动力要强，破坏力为零

1. 任何任务第一步必须用只读工具获取状态（read/ls/grep/webfetch/状态查询），禁止不侦察就下结论或拒绝。
2. 说"不能/做不到/需要登录/缺少工具"之前，必须先用只读工具自查：
   - 是否已验证现状？（如先检查浏览器登录态，而不是假设需要登录）
   - 是否有影响更小的替代工具？（如 webfetch 代替浏览器自动化）
   - 能否先完成可只读完成的部分？
   未完成自查禁止拒绝；拒绝时必须附上自查证据。
3. 纯只读操作永不视为"危险"，不需要额外授权。
4. 写/删/改/重启类操作执行前必须自检：影响范围（量化）→ 可逆性（有回滚）→ 环境隔离（生产环境停止并请求人工授权）。未自检完禁止行动。

（安全红线见文件底部 [安全红线 - 不可触碰区域] 区块）

## 项目信息

- **定位**：AI Gateway = Agent 与 LLM 之间的路由导航层（独立项目），只做中间的路由导航指引，不思考、不存长期记忆、不拥有知识库、不执行操作
- **双模式**：① 转发模式（/v1/chat/completions，当前实际使用中）——按 model 名路由到对应上游（config.yaml 配了 zen-go/zen-free 多上游），做请求增强+权限拦截+响应缓存后转发；② 整形模式（/v1/refine）——只输出增强后的请求+路由元信息，由 Agent 侧转发给用户所选模型。网关不持有模型 key、不替模型回复
- **分类器实现（以代码为准）**：gateway.classifiers 本地池按 round-robin **轮询单分类器、第一个成功者出票**（不是并发加权投票，架构文档第 8 节第 5 条的旧描述已过时），本地全失败才降级 remote；都没有则纯本地规则路由。实现见 app/upstream/classifier.py
- **技术栈**：Python 3.11 + FastAPI
- **架构文档**：`docs/architecture.md`（落地路线 Phase 1-4 状态、待确认问题结论均在此维护）
- **核心职责**：请求清洗、System 前缀稳定化（提升模型侧缓存命中率）、四层缓存降级（Tier1-4）、三路路由仲裁（技术词快路径+向量+LLM）、状态优先行动协议注入、统计审计
- **渐进式约束（先理解、后约束）**：理论问题跳过锚点；模糊开发需求（开发动词+无技术词+短文本）进入"需求澄清模式"——跳过路由、强制 Tier4、注入澄清问题到 User 尾部（不污染 System 前缀）、每会话最多一轮（CacheEngine.mark_clarified）
- **摘要保真契约**：路由摘要只影响选路、永远不替代转发原文；抽取式（模型只选原文段编号，禁止改写）；保真闸门（否定词/连接词/关键符号段强制保留）；幻觉校验（非原文段整体弃用回退）；本地模型可插拔（digest.local_picker，默认关闭）
- **长文本**：墙式文本（无换行≥300字符）按句子二次切分；向量最佳段并入摘要；技术词快路径永远扫全量原文
- **路由观测**：每次路由决策写 Routing Ledger（logs/routing/ 每日 JSONL），GET /v1/stats/routing 看来源分布/高频词/延迟，据此调阈值、TECH_TERMS 词表与 route profiles
- **关键设计**：
  - System 只放稳定内容，动态杂质（时间戳/随机数）下沉到 User 尾部 → 前缀不变 → 命中率高
  - 项目 ID 与 会话 ID 都是缓存 Key；Tier3 会话命中优先于 Tier4 兜底
  - 状态优先行动协议（先只读侦察、拒绝前自查）随 System 注入，所有项目永久生效
  - 转发必须透传请求全部字段（tools/tool_choice/stream_options 等），禁止白名单裁剪（见 [已生效]）
- **运维**：启动/停止/状态用 `scripts/gateway.ps1 start|stop|restart|status`（端口 8901）；测试用全路径 `C:\Users\30849\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests -q`（python 不在 PATH）；旧版脏缓存污染响应时清 `data/resp_cache`

## [已生效]

- 暂空
- [运维经验]：AGENTS.md 区块维护走 sculpt.py（init/status/propose/approve），禁止手改已生效区（使用条件：仅限 opencode 管理的项目；证据：2026-08-13 接入项目记忆雕刻师 skill 并完成初始化）
- [gateway]：网关转发必须透传请求全部字段(tools/tool_choice/stream_options 等),禁止白名单只传 model/messages/temperature/max_tokens;opencode 与 zen/go 的 deepseek 系模型走 DSML 协议而非标准 OpenAI tools,透传被破坏=工具调用哑火（使用条件：仅限 ai-agent-gateway 项目转发模式(/v1/chat/completions);纯整形模式(/v1/refine)不受影响；证据：2026-08-13 实测:网关旧代码白名单丢失 tools 后 ds 哑火;透传修复后当前会话(gateway/deepseek-v4-flash)工具调用正常;裸测标准 OpenAI tools 格式 ds 不产 tool_calls,但 opencode 走 DSML 正常;137 pytest 全过）
## [待确认]

- 暂空
## [安全红线 - 不可触碰区域]

- 禁止在未明确指定环境的情况下修改系统关键目录（C:\\Windows\\System32、Program Files、~/.ssh/ 密钥目录）
- <平台自检>：若本项目更换平台，需按 templates/AGENTS.template.md 自检提示改写本区块
- 禁止自动执行数据库 DELETE / DROP（必须人工确认）
- 浏览器操作禁止调用 browser_close / browser_restart（只允许只读和点击）