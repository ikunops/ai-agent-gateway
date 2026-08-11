import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.preprocess import clean_messages, normalize_text
from app.layers.system_builder import (
    build_system,
    extract_dynamic_impurities,
    is_theoretical_query,
    is_vague_development_request,
    reorganize_messages,
)


def test_normalize_text():
    assert normalize_text("检查  k8s  节点\r\n\r\n") == "检查 k8s 节点"


def test_clean_messages():
    msgs = [{"role": "user", "content": "  hello   world\r\n"}]
    out = clean_messages(msgs)
    assert out[0]["content"] == "hello world"


def test_extract_impurities():
    text = "now is 2026-08-11T09:23:23.399Z, uuid 6a3268cb-4589-4846-a507-6a79a36c0b84, 1234567890"
    _, found = extract_dynamic_impurities(text)
    assert len(found) >= 3


def test_build_system_layers():
    s = build_system("anchor", "project", "session", "note")
    assert "anchor" in s and "project" in s and "session" in s and "note" in s


def test_reorganize_stable_prefix():
    anchor = "A" * 200
    m1 = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": f"2026-08-11T09:23:23Z 帮我查日志"},
    ]
    m2 = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": f"2026-08-12T10:00:00Z 帮我查日志"},
    ]
    r1 = reorganize_messages(m1, anchor_prompt=anchor)
    r2 = reorganize_messages(m2, anchor_prompt=anchor)
    assert r1[0]["content"] == r2[0]["content"]


def test_dynamic_impurity_moved_to_tail():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "看看 2026-08-11T09:00:00Z 的部署状态"},
    ]
    out = reorganize_messages(msgs, anchor_prompt="ANCHOR")
    joined = " ".join(m.get("content", "") for m in out)
    assert "[动态信息:" in joined


def test_system_role_desensitized_user_untouched():
    sys_msg = {"role": "system", "content": "当前时间 2026-08-11T09:00:00.123Z"}
    user_msg = {"role": "user", "content": "查订单 550e8400-e29b-41d4-a716-446655440000 状态"}
    out = clean_messages([sys_msg, user_msg])
    assert "[TIMESTAMP]" in out[0]["content"]
    assert "550e8400-e29b-41d4-a716-446655440000" in out[1]["content"]


def test_user_uuid_never_desensitized():
    user_msg = {"role": "user", "content": "订单 550e8400-e29b-41d4-a716-446655440000 状态如何"}
    out = clean_messages([user_msg])
    assert "550e8400-e29b-41d4-a716-446655440000" in out[0]["content"]


def test_code_block_preserved():
    code = "```yaml\nservice:\n    name: app\n    port: 8080\n```"
    out = clean_messages([{"role": "user", "content": code}])
    assert "    port: 8080" in out[0]["content"]


def test_code_block_spacing_kept():
    code = "def f():\n    return 1"
    out = clean_messages([{"role": "user", "content": code}])
    assert out[0]["content"] == code


def test_theoretical_query_detection():
    msgs = [{"role": "user", "content": "什么是 CAP 定理"}]
    assert is_theoretical_query(msgs) is True
    msgs2 = [{"role": "user", "content": "帮我查一下 k8s 节点状态"}]
    assert is_theoretical_query(msgs2) is False


def test_skip_anchor_for_theoretical():
    msgs = [{"role": "user", "content": "解释一下 CAP 理论"}]
    out = reorganize_messages(msgs, anchor_prompt="状态优先行动协议...", skip_anchor=True)
    assert "状态优先行动协议" not in out[0]["content"]


def test_system_length_cap():
    long = "A" * 10000
    s = build_system(long, "", "", "", max_chars=1000)
    assert len(s) <= 1000 + 40
    assert "截断" in s


def test_vague_detection_positive():
    msgs = [{"role": "user", "content": "开发一个手机清理工具"}]
    assert is_vague_development_request(msgs) is True
    assert is_vague_development_request([{"role": "user", "content": "帮我写一个手机清理工具"}]) is True
    assert is_vague_development_request([{"role": "user", "content": "想搭一个监控面板"}]) is True


def test_vague_detection_with_tech_word_negative():
    assert is_vague_development_request([{"role": "user", "content": "用python写一个清理工具"}]) is False
    assert is_vague_development_request([{"role": "user", "content": "帮我写一个web工具"}]) is False


def test_vague_detection_negation():
    assert is_vague_development_request([{"role": "user", "content": "不需要开发手机清理工具"}]) is False
    assert is_vague_development_request([{"role": "user", "content": "别实现这个功能"}]) is False


def test_vague_detection_theoretical_excluded():
    assert is_vague_development_request([{"role": "user", "content": "解释一下怎么开发一个手机清理工具"}]) is False


def test_vague_detection_long_text_negative():
    long_text = "开发一个手机清理工具，" + "我们需要先分析需求，然后设计架构，接着写代码。" * 5
    assert is_vague_development_request([{"role": "user", "content": long_text}]) is False


def test_vague_detection_last_message_only():
    msgs = [
        {"role": "user", "content": "开发一个手机清理工具"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "用 python 写吧"},
    ]
    assert is_vague_development_request(msgs) is False


def test_vague_detection_list_content():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "开发一个手机清理工具"}]}]
    assert is_vague_development_request(msgs) is True


def test_clarification_hint_appended_to_user_tail():
    msgs = [{"role": "user", "content": "开发一个手机清理工具"}]
    out = reorganize_messages(
        msgs,
        anchor_prompt="ANCHOR",
        skip_anchor=True,
        clarification_hint="[需求澄清模式]\n请先输出澄清问题",
    )
    assert out[0]["content"] == "ANCHOR" or "需求澄清模式" not in out[0]["content"]
    last = out[-1]["content"]
    assert "[需求澄清模式]" in last
    assert last.startswith("开发一个手机清理工具")


def test_clarification_hint_only_last_user_message():
    msgs = [
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "开发一个手机清理工具"},
    ]
    out = reorganize_messages(msgs, anchor_prompt="ANCHOR", clarification_hint="HINT")
    assert "HINT" in out[-1]["content"]
    assert "HINT" not in out[1]["content"]


def test_impurities_capped():
    text = " ".join(f"2026-08-11T09:00:{i:02d}Z" for i in range(100))
    _, found = extract_dynamic_impurities(text)
    assert len(found) == 20


def test_impurities_deduped():
    text = "2026-08-11T09:00:00Z and 2026-08-11T09:00:00Z again"
    _, found = extract_dynamic_impurities(text)
    assert len(found) == 1

