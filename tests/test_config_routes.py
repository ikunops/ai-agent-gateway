import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import UpstreamRoute, load_config


@pytest.fixture()
def cfg(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        """
upstream:
  default:
    base_url: "https://opencode.ai/zen/v1"
    api_key_env: ""
    default_model: "deepseek-v4-flash-free"
  routes:
    - name: zen-go
      base_url: "https://opencode.ai/zen/go/v1"
      api_key_env: "OPENCODE_GO_API_KEY"
      default_model: "deepseek-v4-flash"
      match_models:
        - glm-5.2
        - kimi-k3
        - deepseek-v4-pro
    - name: zen-free
      base_url: "https://opencode.ai/zen/v1"
      api_key_env: ""
      default_model: "deepseek-v4-flash-free"
      match_suffix: "-free"
""",
        encoding="utf-8",
    )
    return load_config(yaml_path)


def test_routes_parsed(cfg):
    assert [r.name for r in cfg.upstream_routes] == ["zen-go", "zen-free"]


def test_resolve_exact_model(cfg):
    route = cfg.resolve_upstream("glm-5.2")
    assert isinstance(route, UpstreamRoute)
    assert route.name == "zen-go"
    assert route.base_url == "https://opencode.ai/zen/go/v1"
    assert route.api_key_env == "OPENCODE_GO_API_KEY"


def test_resolve_suffix_model(cfg):
    route = cfg.resolve_upstream("deepseek-v4-flash-free")
    assert route.name == "zen-free"
    assert route.base_url == "https://opencode.ai/zen/v1"


def test_resolve_unknown_falls_back_to_default(cfg):
    route = cfg.resolve_upstream("totally-unknown-model")
    assert route.name == "default"
    assert route.base_url == "https://opencode.ai/zen/v1"


def test_real_config_has_go_route():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    names = [r.name for r in cfg.upstream_routes]
    assert "zen-go" in names and "zen-free" in names
    assert cfg.resolve_upstream("glm-5.2").name == "zen-go"
    assert cfg.resolve_upstream("deepseek-v4-flash-free").name == "zen-free"
