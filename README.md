# AI Gateway

Agent 与 LLM 之间的路由导航层：不思考、不记忆、不执行，只负责"用户会话进来 → 交给大模型之前"这一段——清洗、上下文、路由、System 构造、命中率提升。

> 状态：Phase 1 + Phase 2 已完成（MVP 转发 + 前缀稳定化 + 精确缓存 + 四层降级 + 项目感知 + 统计审计）
> 详细架构设计见 [docs/architecture.md](docs/architecture.md)

## 定位

```
┌─────────────┐      OpenAI 兼容 API       ┌──────────────┐      上游调用       ┌──────────────┐
│  Agent 侧   │ ────────────────────────► │  AI Gateway  │ ────────────────► │  LLM 侧      │
│  (可插拔)    │  POST /v1/chat/completions │  (本项目)     │   转发             │  (可插拔)     │
└─────────────┘                           └──────────────┘                    └──────────────┘
   opencode / 其他 Agent                  仅做路由导航指引                    DeepSeek / 其他模型
```

- **Agent 侧可插拔**：opencode 等任意 OpenAI 兼容客户端（配 base_url 即接入，客户端零改动）
- **LLM 侧可插拔**：DeepSeek 等任意模型端点（加一个 upstream 配置即可）

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
3. **L2 决策路由**（Phase 3 预留）：三路并行仲裁（语义向量 + 知识库 + 轻量 LLM 分类），加权取高，决定模型选择与 System 前缀来源
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

# 2. 配置上游密钥（环境变量或 config.yaml）
set DEEPSEEK_API_KEY=sk-xxx

# 3. 启动
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080

# 4. 验证
curl http://127.0.0.1:8080/v1/health
```

## 对接 opencode

在 opencode 配置（`opencode.jsonc`）中把 provider 指向网关：

```jsonc
{
  "provider": {
    "deepseek-via-gateway": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://127.0.0.1:8080/v1",
        "apiKey": "gateway-dev-key"
      },
      "models": { "deepseek-chat": {} }
    }
  }
}
```

opencode 侧零代码改动，`X-Project-Id` / `X-Session-Id` 由网关从请求自动解析。

## API 一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/chat/completions` | POST | 主代理接口（OpenAI 兼容，流式+非流式） |
| `/v1/models` | GET | 模型列表 |
| `/v1/health` | GET | 健康检查 |
| `/v1/projects` | POST | 注册项目（绑定 project_id → AGENTS.md 路径） |
| `/v1/projects` | GET | 项目列表 |
| `/v1/projects/{id}` | DELETE | 注销项目 |
| `/v1/cache/clear` | POST | 清空精确响应缓存 |
| `/v1/stats/hits` | GET | 统计（精确缓存命中率 + 前缀重叠 + Tier 分布） |

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

覆盖：清洗、前缀稳定化、动态杂质下沉、四层缓存降级、精确缓存（含流式/非流式共享命中）、SSE 合成、项目注册表持久化、鉴权、统计审计。当前 **34 个测试全部通过**。

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
│   │   ├── system_builder.py   # L4 前缀稳定化 / 动态杂质下沉
│   │   ├── cache.py            # L3 四层降级 / 技术栈标签提取
│   │   ├── registry.py         # 项目注册表（project_id → AGENTS.md）
│   │   ├── response_cache.py   # 精确响应缓存 / SSE 合成 / 费用估算
│   │   └── stats.py            # 统计 / 审计
│   └── upstream/
│       └── llm.py              # 上游转发（流式 + 非流式）
├── tests/                      # 34 个测试
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
