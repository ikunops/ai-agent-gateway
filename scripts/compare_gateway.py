"""对比：同一长文本 直连 vs 过网关（整形模式 /v1/refine）。

用法：
    py -X utf8 scripts/compare_gateway.py        # 默认 mock：直连 vs 网关整形（不调任何模型）
    set DEEPSEEK_API_KEY=sk-xxx
    py -X utf8 scripts/compare_gateway.py --real # 打真实 DeepSeek，对比直连与网关转发后的最终回复

整形模式：网关不持有模型 key、不选模型——返回"增强后的请求"，由 Agent 侧转发给用户当前模型。
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Config, UpstreamConfig, load_config  # noqa: E402
from app.layers.system_builder import (  # noqa: E402
    is_theoretical_query,
    is_vague_development_request,
)
from app.layers.text_analysis import extract_tech_terms, split_segments  # noqa: E402
from app.main import create_app  # noqa: E402

LONG_TEXT = """制作一个标准的 Skill，核心是**把“反复说的话”变成“AI默认遵守的工作规范”**。它不是一段临时提示词，而是一个结构化的能力包。

### 📁 第一步：创建标准目录结构
Skill 的最小单元是一个**目录**，而不是单个文件。标准结构如下：

```
my-skill/
├── SKILL.md      # 必须：核心指令与元数据
├── scripts/      # 可选：可执行脚本 (Python/Shell等)
├── references/   # 可选：参考文档、规范
└── assets/       # 可选：模板、图片等资源
```

### 📝 第二步：编写核心文件 SKILL.md
这是 Skill 的入口和“说明书”，包含两部分：

**1. YAML 元数据 (Frontmatter)**
用 `---` 包裹，是AI判断何时调用你的关键。
*   **`name`**：必需。唯一标识符，**只限小写字母、数字和连字符(-)**，且**必须和父目录名一致**。
*   **`description`**：必需。描述Skill**做什么**以及**何时触发**，包含动作、场景和关键词。
*   **其他可选字段**：`version`、`license`、`compatibility`、`allowed-tools`等。

**2. Markdown 正文**
元数据后编写，是AI触发后加载的详细指令：
*   **明确目标与边界**：开门见山说明解决什么问题。
*   **详细执行流程**：写出清晰、可操作的步骤。
*   **固定输出格式**：明确报告、代码的格式要求。
*   **引用外部资源**：如需复杂规则，指引AI去 `references/` 等目录查找。

### 🧠 第三步：遵循核心设计原则
*   **场景驱动**：从**重复3次以上**的真实痛点入手。**先做小，再做大**。
*   **渐进式披露**：元数据常驻内存；正文触发才加载；脚本和参考资料按需调用，**以节省上下文资源**。
*   **具体可操作**：避免笼统描述。用“**1. 复现问题；2. 提出假设...**”代替“认真修复Bug”。
*   **基于真实专业经验**：不要只依赖AI的通用知识，要喂给它**领域特定的上下文**。

### 🚀 第四步：创建、测试与迭代
*   **手工创建**：按上述结构手动创建文件夹和文件。
*   **借助工具**：在支持 Skill 的 IDE（如 Claude Code、Cursor）中，可使用 `skill-creator` 等内置工具对话式生成。
*   **测试与迭代**：在实际场景中测试，观察触发是否准确、执行是否符合预期，并持续优化。

总的来说，创建标准Skill就是围绕 `SKILL.md` 构建一个结构清晰、职责单一的专业能力包。它是对抗重复劳动、沉淀专业经验的有效方式。

如果想针对某个具体场景（比如代码审查、生成报告）动手尝试，可以随时再问我，我们一起看怎么写～"""


class MockUpstream(BaseHTTPRequestHandler):
    calls = 0
    last_body = {}

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) or b"{}"
        body = json.loads(raw)
        type(self).last_body = body
        is_judge = any(
            "路由分类器" in str(m.get("content", ""))
            for m in body.get("messages", [])
            if isinstance(m, dict)
        )
        if is_judge:
            data = {
                "id": "judge-1", "model": body.get("model", "m"),
                "choices": [{"message": {"role": "assistant", "content": "generic"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9},
            }
        else:
            data = {
                "id": "m1", "model": body.get("model", "m"),
                "choices": [{"message": {"role": "assistant", "content": "mock reply"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _clip(text: str, n: int = 300) -> str:
    return text[:n] + ("…" if len(text) > n else "")


def main() -> None:
    use_real = bool(os.environ.get("DEEPSEEK_API_KEY", "")) and "--real" in sys.argv

    real_cfg = load_config()
    tmp = Path(tempfile.mkdtemp(prefix="gateway-compare-"))

    if use_real:
        base_url = real_cfg.upstream.base_url
    else:
        # mock 上游只为让路由的 LLM 分类器有地方可问；/v1/refine 本身不调用任何模型
        # 分类器有 api_key 守卫，mock 不校验密钥，随便给一个值让它真实走通
        os.environ["DEEPSEEK_API_KEY"] = "mock-key"
        server = HTTPServer(("127.0.0.1", 0), MockUpstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}/v1"

    cfg = Config(
        api_keys={"default": "demo-key"},
        upstream=UpstreamConfig(
            base_url=base_url, api_key_env="DEEPSEEK_API_KEY", default_model="deepseek-chat"
        ),
        anchor_prompt=real_cfg.anchor_prompt,
        clarify_prompt=real_cfg.clarify_prompt,
        routes=real_cfg.routes,
        router_threshold=real_cfg.router_threshold,
        router_cache_ttl=real_cfg.router_cache_ttl,
        tier3_max_sessions=real_cfg.tier3_max_sessions,
        audit_dir=str(tmp / "logs"),
        data_dir=str(tmp / "data"),
    )
    client = TestClient(create_app(cfg))

    segs = split_segments(LONG_TEXT)
    terms = extract_tech_terms(LONG_TEXT)
    print("=" * 62)
    print("输入分析")
    print(f"  文本长度: {len(LONG_TEXT)} 字符 | 切段数: {len(segs)} | 技术词: {terms or '无'}")
    print(f"  理论问题: {is_theoretical_query([{'role': 'user', 'content': LONG_TEXT}])}"
          f" | 模糊开发需求: {is_vague_development_request([{'role': 'user', 'content': LONG_TEXT}])}"
          "（>60 字符 → 不触发澄清）")
    print("-" * 62)

    print("[直连] 客户端消息原样 → 用户当前模型")
    print(f"  模型收到: 1 条裸 user 消息（无 system、无路由、无上下文注入）")
    print(f"  user 前200字符: {_clip(LONG_TEXT, 200)}")
    print("-" * 62)

    print("[网关整形 /v1/refine] 清洗 → 意图检测 → 路由 → Tier → System 构造（网关不调任何模型）")
    resp = client.post(
        "/v1/refine",
        headers={"X-API-Key": "demo-key", "X-Session-Id": "cmp1"},
        json={"messages": [{"role": "user", "content": LONG_TEXT}], "model": "user-current-model"},
    )
    if resp.status_code != 200:
        print(f"  异常: {resp.status_code} {resp.text[:200]}")
        return
    data = resp.json()
    refined = data["refined"]
    print(f"  返回消息数: {len(refined['messages'])}（1 system + {len(refined['messages']) - 1} user）")
    print(f"  refined.model: {refined['model']}（原样回传，opencodego 决定最终模型）")
    print(f"  System 前缀(前400):\n    {refined['messages'][0]['content'][:400]}")
    print(f"  user 前200字符: {_clip(refined['messages'][-1]['content'], 200)}")
    print(f"  meta: {json.dumps(data['meta'], ensure_ascii=False)}")
    ledger_file = sorted((tmp / "logs" / "routing").glob("routing-*.jsonl"))
    rec = json.loads(ledger_file[-1].read_text(encoding="utf-8").strip().splitlines()[-1])
    print(f"  Ledger: source={rec['source']} route={rec['route'] or '(none)'} "
          f"tier={rec['tier']} fidelity_forced={rec['fidelity_forced']} "
          f"judge_latency_ms={rec['judge_latency_ms']}")
    print("=" * 62)

    print("差异总结")
    print("  直连: 模型收到裸 user，自由发挥，无约束无画像")
    print("  过网关: 模型收到 1 条 system（锚点"
          + (" + 领域画像" if rec["tier"] == 1 else "")
          + "） + 1 条重排 user；路由/保真/Ledger 全部留痕")
    print("  网关自身: 零模型调用" + ("" if use_real else "（分类器走 mock）")
          + " | 模型选择: 完全由 opencodego 按用户切换决定")

    if not use_real:
        server.shutdown()


if __name__ == "__main__":
    main()
