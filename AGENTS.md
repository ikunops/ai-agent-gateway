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

### 安全红线（不可触碰区域）

- 禁止在未明确指定环境的情况下修改 `/etc/` 或 `~/.ssh/` 目录
- 禁止自动执行数据库 `DELETE` / `DROP`（必须人工确认）
- 浏览器操作禁止调用 `browser_close` / `browser_restart`（只允许只读和点击）

## 项目信息

- **定位**：AI Gateway = Agent 与 LLM 之间的路由导航层（独立项目），只做中间的路由导航指引，不思考、不存长期记忆、不拥有知识库、不执行操作
- **主形态 = 纯整形**：网关不持有任何模型 key、不选模型、不替模型回复。POST /v1/refine 返回"增强后的请求（refined.messages）+ 路由元信息（meta）"，由 Agent 侧（opencodego）转发给用户当前选择的模型（用户切模型只影响 Agent 侧，网关无感知）。转发模式（/v1/chat/completions）仅在有 upstream.base_url 配置时可用（config.yaml 默认配了 DeepSeek，是转发模式）
- **分类器由用户自定**：gateway.classifiers（任意 OpenAI 兼容端点，本地 Ollama 免密钥 / DeepSeek / 中转站）→ 并发调用 + 加权投票交叉验证，agreement 作为路由得分；未配 classifiers 时回退 upstream.default 单分类器；都没有则纯本地规则路由。分类器实现见 app/upstream/classifier.py（ClassifierClient / ClassifierEnsemble）
- **技术栈**：Python 3.11 + FastAPI
- **架构文档**：`docs/architecture.md`（评审通过前不写业务代码）
- **核心职责**：请求清洗、System 前缀稳定化（提升模型侧缓存命中率）、四层缓存降级（Tier1-4）、三路路由仲裁（技术词快路径+向量+LLM）、状态优先行动协议注入、统计审计
- **渐进式约束（先理解、后约束）**：理论问题跳过锚点；模糊开发需求（开发动词+无技术词+短文本）进入"需求澄清模式"——跳过路由、强制 Tier4、注入澄清问题到 User 尾部（不污染 System 前缀）、每会话最多一轮（CacheEngine.mark_clarified）
- **摘要保真契约**：路由摘要只影响选路、永远不替代转发原文；抽取式（模型只选原文段编号，禁止改写）；保真闸门（否定词/连接词/关键符号段强制保留）；幻觉校验（非原文段整体弃用回退）；本地模型可插拔（digest.local_picker，默认关闭）
- **长文本**：墙式文本（无换行≥300字符）按句子二次切分；向量最佳段并入摘要；技术词快路径永远扫全量原文
- **路由观测**：每次路由决策写 Routing Ledger（logs/routing/ 每日 JSONL），GET /v1/stats/routing 看来源分布/高频词/延迟，据此调阈值、TECH_TERMS 词表与 route profiles
- **关键设计**：
  - System 只放稳定内容，动态杂质（时间戳/随机数）下沉到 User 尾部 → 前缀不变 → 命中率高
  - 项目 ID 与 会话 ID 都是缓存 Key；Tier3 会话命中优先于 Tier4 兜底
  - 状态优先行动协议（先只读侦察、拒绝前自查）随 System 注入，所有项目永久生效
- **落地路线**：Phase 1 MVP（OpenAI 兼容转发+鉴权+协议注入+会话管理）→ Phase 2 缓存降级+前缀稳定化 → Phase 3 三路路由+模型选择 → Phase 4 统计审计+权限分级
- **待确认**：docs/architecture.md 第 8 节（命中率量化/会话ID来源/存储/流式一期必做），评审后进入 Phase 1
- **交付后**：放到 Git 仓库，需要时再用于其他场景
