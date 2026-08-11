import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.preprocess import clean_messages, normalize_text
from app.layers.system_builder import (
    build_system,
    extract_dynamic_impurities,
    is_theoretical_query,
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

