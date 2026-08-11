import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.permissions import check_permission, scan_danger


def test_no_danger_allowed():
    r = check_permission("帮我查一下 Oracle 进程状态", "L0")
    assert r["action"] == "allow" and r["allowed"]


def test_rm_rf_blocked_l1():
    r = check_permission("执行 rm -rf /var/log 清理日志", "L1")
    assert r["action"] == "block" and not r["allowed"]
    assert any("rm -rf" in m["label"] for m in r["matched"])


def test_rm_rf_confirm_l2():
    r = check_permission("执行 rm -rf /var/log 清理日志", "L2")
    assert r["action"] == "confirm" and r["allowed"]


def test_drop_database_blocked():
    r = check_permission("DROP DATABASE production", "L1")
    assert r["action"] == "block"
    assert any("DROP" in m["label"] for m in r["matched"])


def test_l0_read_only_write_blocked():
    r = check_permission("用 reg delete 清理注册表项", "L0")
    assert r["action"] == "block"


def test_confirm_requires_l2_level_header_override():
    r = check_permission("shutdown -h now", "L0")
    assert r["action"] == "block" and r["required"] == "L2"
    r2 = check_permission("shutdown -h now", "L2")
    assert r2["action"] == "confirm"


def test_scan_danger_multi():
    found = scan_danger("先执行 del /s /q C:\\temp，再重启服务器 reboot")
    labels = [f["label"] for f in found]
    assert any("del /s" in l for l in labels)
    assert any("重启" in l for l in labels)


def test_unknown_level_defaults_l1():
    r = check_permission("rm -rf /x", "L9")
    assert r["level"] == "L1" and r["action"] == "block"


def test_empty_text_allow():
    r = check_permission("", "L0")
    assert r["action"] == "allow" and r["matched"] == []
