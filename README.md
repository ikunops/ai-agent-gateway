# AI Gateway

Agent 与 LLM 之间的路由导航层：不思考、不记忆、不执行，只负责"用户会话进来 → 交给大模型之前"这一段——清洗、上下文、路由、System 构造、命中率提升。

**主形态是纯整形**：网关不持有任何模型 key、不选模型、不替模型回复。`POST /v1/refine` 返回"增强后的请求"，由 Agent 侧（opencodego）转发给用户当前选择的模型。

> 状态：Phase 1 + Phase 2 已完成（纯整形 refine + 前缀稳定化 + 精确缓存 + 四层降级 + 项目感知 + 统计审计 + 路由明细账）
> 详细架构设计见 [docs/architecture.md](docs/architecture.md)

## 定位

```
┌─────────────┐   原始请求   ┌──────────────┐   增强后的请求    ┌──────────────┐
│  Agent 侧   │ ──────────► │  AI Gateway  │ ──────────────► │  Agent 侧     │
│ (opencodego)│             │  (本项目)     │   refined.messages│ (同侧转发)    │
└─────────────┘             └──────────────┘                  └──────┬───────┘
                                                                    │ 用户当前选择的模型
                                                                    ▼
                                                             goapi / 任何模型
```

- **网关不持有模型 key**：模型由用户在 Agent 侧切换，网关无感知、不绑定
- **主端点 `POST /v1/refine`**：请求整形，零模型依赖；转发模式（配了 `upstream.base_url`）可选
- **多分类器交叉验证（可选）**：`gateway.classifiers` 由你自定任意 OpenAI 兼容端点（Ollama 免费本地 / DeepSeek / 中转站），网关并发调用 + 加权投票，`agreement` 作为路由得分；不配则纯本地规则路由

## 总体架构

```
┌─────────────┐
│   Agent     │ opencode / 其他
└──────┬──────┘
       │ POST /v1/chat/completions
       ▼
┌────────────────────────────────────────────────────────────────┐
│                        AI Gateway                              │
│                                                                │
│  L1 预处理 ──► 精确缓存 ──► L2 决策路由 ──► L3 缓存降级 ──► L4 System构造 ──► L5 出口
│  鉴权/限流      (请求hash)    三路仲裁(预留)   四层匹配        前缀稳定化       转发/统计/审计
│  清洗/归一化    HIT直接返回   向量+KB+LLM     Tier1-4         动态杂质下沉
│  会话/项目解析                                项目ID/会话ID
│                                                                │
└────────────────────────────────────┬───────────────────────────┘
                                     │ 转发
                                     ▼
                              DeepSeek / 其他模型
```

### 请求处理流水线

1. **L1 预处理**：API Key 鉴权 → 文本归一化（换行/空格/异常字符）→ 解析 `X-Project-Id` / `X-Session-Id`
2. **精确缓存检查**：请求体 hash 命中 → 直接返回缓存（流式/非流式共享，SSE 合成），零上游消耗
3. **L2 决策路由**：三路并行仲裁（技术词快路径 + 语义向量 + 轻量 LLM 分类），长文本分段取 max（墙式文本按句子二次切分，向量最佳段并入摘要），决定模型选择与 System 前缀来源；模糊开发需求（如"开发一个手机清理工具"）跳过路由直接进入**需求澄清模式**（每会话一轮，注入澄清问题到 User 尾部）；路由摘要走**保真契约**（抽取式选段 + 否定词/连接词/符号段强制保留 + 幻觉校验），可插拔本地免费模型（Ollama，`digest.local_picker`，默认关闭）；每次路由决策写入 **Routing Ledger**（`/v1/stats/routing` 查看聚合，运行后据此调优）
4. **L3 四层缓存降级**（永不落空）：
   | Tier | 来源 | Key |
   |---|---|---|
   | Tier1 | 全球技术栈通用规范 | 路由标签（如 java/mysql/k8s） |
   | Tier2 | 项目 AGENTS.md（注册表映射） | project_id |
   | Tier3 | 会话摘要（每轮回填） | session_id |
   | Tier4 | 空 System 兜底 | - |
5. **L4 System 构造（前缀稳定化）**：System 只放稳定内容（锚点协议 + Tier 命中产物 + 路由上下文），时间戳/随机数等动态杂质自动下沉到 User 尾部 → 模型侧 prompt 缓存命中率提升
6. **L5 出口**：流式/非流式转发 → 缓冲回填精确缓存 → 会话摘要回填 Tier3 → 统计 + 审计落盘

### 状态优先行动协议

锚点随 System 自动注入（Tier2 家风命中即生效），对所有项目永久生效：

- **状态优先**：任何任务第一步必须用只读工具获取状态，禁止不侦察就下结论或拒绝
- **拒绝前自查**：说"不能/做不到/需要登录"之前必须先只读自查并附证据，拒绝是最后手段
- **行动前自查**：写/删/改/重启前自检影响范围、可逆性、环境隔离

完整协议与安全红线见 `config.yaml` 的 `gateway.anchor_prompt`。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（默认纯整形模式：零上游依赖、零模型 key）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080

# 3. 验证
curl http://127.0.0.1:8080/v1/health
```

## 对接 opencodego

```bash
# 1. 调 /v1/refine 拿增强后的请求
curl -X POST http://127.0.0.1:8080/v1/refine \
  -H "X-API-Key: gateway-dev-key" \
  -H "X-Project-Id: myproj" -H "X-Session-Id: sess1" \
  -d '{"messages": [{"role": "user", "content": "开发一个手机清理工具"}], "model": "用户当前模型"}'

# 2. 把响应里的 refined.messages 原样转发给用户当前选择的模型
#    （模型切换完全在 Agent 侧，网关无感知）
```

响应示意：`refined.messages`（含网关构造的 System 前缀）+ `meta`（路由标签/Tier/澄清模式/保真统计）。

## API 一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/refine` | POST | ★主端点：请求整形（清洗/路由/System 构造），零模型依赖，返回增强请求+路由元信息 |
| `/v1/chat/completions` | POST | 可选转发模式（需配置 `upstream.base_url`，OpenAI 兼容，流式+非流式） |
| `/v1/models` | GET | 模型列表（整形模式返回空） |
| `/v1/health` | GET | 健康检查 |
| `/v1/projects` | POST | 注册项目（绑定 project_id → AGENTS.md 路径） |
| `/v1/projects` | GET | 项目列表 |
| `/v1/projects/{id}` | DELETE | 注销项目 |
| `/v1/cache/clear` | POST | 清空精确响应缓存 |
| `/v1/stats/hits` | GET | 统计（精确缓存命中率 + 前缀重叠 + Tier 分布） |
| `/v1/stats/routing` | GET | 路由决策明细账摘要（来源/标签/Tier 分布 + 高频技术词 + 平均延迟，调优入口） |

请求头：`X-API-Key`（鉴权，必填）、`X-Project-Id`、`X-Session-Id`（缓存 Key）。

## 配置（config.yaml）

```yaml
auth:
  api_keys:
    default: "gateway-dev-key"      # 网关鉴权密钥

upstream:
  default:
    base_url: "https://api.deepseek.com/v1"
    api_key_env: "DEEPSEEK_API_KEY" # 从环境变量读取上游密钥
    default_model: "deepseek-chat"

gateway:
  anchor_prompt: "..."              # 状态优先行动协议 + 安全红线锚点

data:
  dir: "data"                       # 注册表 + 响应缓存持久化目录

audit:
  dir: "logs"                       # 审计 JSONL 落盘目录
  keep_days: 30
```

## 测试

```bash
python -m pytest tests -q
```

覆盖：清洗、前缀稳定化、动态杂质下沉、四层缓存降级、精确缓存（含流式/非流式共享命中）、SSE 合成、项目注册表持久化、鉴权、统计审计、模糊需求澄清模式（含 one-shot）、长文本分段路由（技术词快路径/逐段向量/路由摘要/句子切分/保真闸门/本地抽取模型）、路由决策明细账（Routing Ledger）。当前 **94 个测试全部通过**。

## 目录结构

```
ai-gateway/
├── app/
│   ├── main.py                 # FastAPI 入口 + 管理端点
│   ├── api/
│   │   └── chat.py             # /v1/chat/completions 主代理管线
│   ├── core/
│   │   └── config.py           # 配置加载（yaml + 环境变量）
│   ├── layers/
│   │   ├── preprocess.py       # L1 清洗 / 鉴权 / 会话解析
│   │   ├── text_analysis.py    # 分段（含句子切分）/ 技术词提取 / 路由摘要 / 保真闸门
│   │   ├── system_builder.py   # L4 前缀稳定化 / 动态杂质下沉 / 模糊需求检测
│   │   ├── cache.py            # L3 四层降级 / 技术栈标签提取 / 澄清 one-shot
│   │   ├── registry.py         # 项目注册表（project_id → AGENTS.md）
│   │   ├── response_cache.py   # 精确响应缓存 / SSE 合成 / 费用估算
│   │   ├── routing_log.py      # 路由决策明细账（每日 JSONL + 聚合摘要）
│   │   └── stats.py            # 统计 / 审计
│   └── upstream/
│       └── llm.py              # 上游转发（流式 + 非流式）
├── tests/                      # 94 个测试
├── docs/
│   └── architecture.md         # 完整架构设计文档
├── config.yaml
├── requirements.txt
└── AGENTS.md                   # 本项目家风（状态优先行动协议）
```

## 路线图

| Phase | 内容 | 状态 |
|---|---|---|
| Phase 1 | OpenAI 兼容转发 + 鉴权 + 状态优先协议注入 + 会话管理 | ✅ 完成 |
| Phase 2 | 精确缓存 + 四层降级 + 前缀稳定化 + 项目注册 + 会话摘要回填 | ✅ 完成 |
| Phase 3 | 三路路由仲裁（向量 + 知识库 + 轻量 LLM）+ 轻重模型选择 | ⏳ 计划 |
| Phase 4 | 权限分级 L0/L1/L2 + 出口危险拦截 | ⏳ 计划 |
