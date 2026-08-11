import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import Config, UpstreamConfig
from app.main import create_app

LONG_TEXT = (
    "制作一个标准的 Skill，核心是把反复说的话变成 AI 默认遵守的工作规范。"
    "它不是一段临时提示词，而是一个结构化的能力包。\n\n"
    "第一步：创建标准目录结构。Skill 的最小单元是一个目录，而不是单个文件。\n\n"
    "第二步：编写核心文件 SKILL.md，包含 YAML 元数据和 Markdown 正文。\n\n"
    "第三步：遵循核心设计原则，场景驱动，先做小再做大。\n\n"
    "总的来说，创建标准 Skill 就是围绕 SKILL.md 构建一个结构清晰、职责单一的专业能力包。"
) * 3


@pytest.fixture()
def client(tmp_path):
    """纯整形模式：不配 upstream（零上游依赖、零模型 key）。"""
    cfg = Config(
        api_keys={"default": "test-key"},
        upstream=UpstreamConfig(base_url=""),  # 关键：无上游
        anchor_prompt="ANCHOR PROTOCOL",
        routes={
            "mysql": "MySQL数据库：InnoDB、主从复制、binlog、慢查询、索引优化、锁",
            "generic": "通用问题、编程语言语法、日常IT咨询",
        },
        audit_dir=str(tmp_path / "logs"),
        data_dir=str(tmp_path / "data"),
    )
    return TestClient(create_app(cfg))


def test_refine_basic(client):
    r = client.post(
        "/v1/refine",
        headers={"X-API-Key": "test-key", "X-Session-Id": "r1"},
        json={"messages": [{"role": "user", "content": "帮我看看 mysql 慢查询"}]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["request_id"]
    assert data["refined"]["messages"][0]["role"] == "system"
    assert "ANCHOR PROTOCOL" in data["refined"]["messages"][0]["content"]
    assert data["meta"]["route"]["name"] == "mysql"
    assert data["meta"]["route"]["source"] in ("term", "vector", "cache", "fallback")
    assert data["meta"]["tier"] in (1, 2, 3, 4)
    assert "terms" in data["meta"]


def test_refine_auth_fail(client):
    r = client.post(
        "/v1/refine",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


def test_refine_empty_messages(client):
    r = client.post(
        "/v1/refine",
        headers={"X-API-Key": "test-key"},
        json={"messages": []},
    )
    assert r.status_code == 400


def test_refine_clarify_mode(client):
    r = client.post(
        "/v1/refine",
        headers={"X-API-Key": "test-key", "X-Session-Id": "r2"},
        json={"messages": [{"role": "user", "content": "开发一个手机清理工具"}]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["clarify"] is True
    assert data["meta"]["route"]["source"] == "clarify-skip"
    last = data["refined"]["messages"][-1]["content"]
    assert "[需求澄清模式]" in last


def test_refine_clarify_one_shot(client):
    headers = {"X-API-Key": "test-key", "X-Session-Id": "r3"}
    body = {"messages": [{"role": "user", "content": "开发一个手机清理工具"}]}
    r1 = client.post("/v1/refine", headers=headers, json=body)
    assert r1.json()["meta"]["clarify"] is True
    body2 = {"messages": [{"role": "user", "content": "帮我写一个天气应用"}]}
    r2 = client.post("/v1/refine", headers=headers, json=body2)
    assert r2.json()["meta"]["clarify"] is False


def test_refine_long_text_no_clarify(client):
    r = client.post(
        "/v1/refine",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": LONG_TEXT}]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["clarify"] is False
    assert data["meta"]["segments"] > 5


def test_forward_disabled_in_refine_only_mode(client):
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert r.status_code == 400
    assert "refine" in r.text


def test_models_empty_in_refine_only_mode(client):
    r = client.get("/v1/models", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json()["data"] == []
