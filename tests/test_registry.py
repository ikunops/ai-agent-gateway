import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.registry import ProjectRegistry


def test_register_and_get(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "projects.json"))
    reg.register("p1", "/path/to/p1", "desc")
    assert reg.get("p1")["agents_path"] == "/path/to/p1"
    assert reg.get("p2") is None


def test_persistence(tmp_path):
    store = str(tmp_path / "projects.json")
    reg = ProjectRegistry(store)
    reg.register("p1", "/x")
    reg2 = ProjectRegistry(store)
    assert reg2.get("p1")["agents_path"] == "/x"


def test_unregister(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "projects.json"))
    reg.register("p1", "/x")
    assert reg.unregister("p1") is True
    assert reg.unregister("p1") is False
    assert reg.get("p1") is None


def test_agents_file_dir(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "projects.json"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("rules", encoding="utf-8")
    reg.register("proj", str(proj))
    f = reg.agents_file("proj")
    assert f is not None and f.name == "AGENTS.md"


def test_agents_file_direct(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "projects.json"))
    f = tmp_path / "AGENTS.md"
    f.write_text("rules", encoding="utf-8")
    reg.register("proj", str(f))
    assert reg.agents_file("proj") == f


def test_agents_file_missing(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "projects.json"))
    reg.register("proj", str(tmp_path / "nonexistent"))
    assert reg.agents_file("proj") is None
