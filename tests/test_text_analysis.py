import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.text_analysis import (
    build_routing_digest,
    extract_tech_terms,
    route_name_from_terms,
    split_segments,
)


def test_split_segments_paragraphs():
    segs = split_segments("第一段\n\n第二段 MySQL 延迟\n\n第三段\n\n好的\n\n谢谢")
    assert segs == ["第一段", "第二段 MySQL 延迟", "第三段"]


def test_split_segments_filler_only_falls_back():
    segs = split_segments("好的\n\n谢谢")
    assert segs == ["好的", "谢谢"]


def test_split_segments_empty():
    assert split_segments("") == []
    assert split_segments("   ") == []


def test_split_segments_long_single_block_by_sentence():
    text = "。".join(f"句子{i}" for i in range(300))
    segs = split_segments(text)
    assert len(segs) >= 2
    assert all(len(s) <= 350 for s in segs)


def test_split_segments_long_no_punctuation_chunked():
    text = "log line " * 100
    segs = split_segments(text)
    assert len(segs) >= 2
    assert all(len(s) <= 350 for s in segs)


def test_extract_tech_terms_later_paragraph():
    text = "今天天气不错，下午想整理一下服务器。\n\n然后 MySQL 主从延迟，binlog 堆积严重"
    terms = extract_tech_terms(text)
    assert "mysql" in terms


def test_extract_tech_terms_case_insensitive():
    assert "k8s" in extract_tech_terms("检查 K8S 节点")


def test_extract_tech_terms_word_boundary():
    assert "app" not in extract_tech_terms("apple 很好吃")
    assert "go" not in extract_tech_terms("google 搜索")


def test_extract_tech_terms_longest_first():
    terms = extract_tech_terms("用 c++ 写一个工具")
    assert "c++" in terms


def test_build_routing_digest_includes_term_segment():
    text = "第一段闲聊\n\n第二段闲聊\n\n第三段 MySQL 主从延迟 binlog 堆积\n\n第四段收尾"
    digest = build_routing_digest(text)
    assert "MySQL 主从延迟" in digest
    assert "第一段闲聊" in digest
    assert "第四段收尾" in digest


def test_build_routing_digest_cap():
    text = "第一段\n\n" + "mysql " * 300
    digest = build_routing_digest(text, max_chars=200)
    assert len(digest) <= 201


def test_route_name_from_terms_alias():
    profiles = {"kubernetes": "容器编排", "mysql": "数据库"}
    assert route_name_from_terms("k8s 节点问题", profiles) == "kubernetes"
    assert route_name_from_terms("mysql 慢查询", profiles) == "mysql"
    assert route_name_from_terms("今天天气不错", profiles) is None


LONG_FILLER = "日常闲聊内容。\n\n" * 40


def test_digest_keeps_negation_via_guard():
    neg = "这段明确说不要用这个方案，因为风险太高"
    digest = build_routing_digest(LONG_FILLER + neg + "\n\n" + LONG_FILLER)
    assert neg in digest


def test_digest_keeps_connector_via_guard():
    conn = "这个系统会迁移 A 和 B 到新机房"
    digest = build_routing_digest(LONG_FILLER + conn + "\n\n" + LONG_FILLER)
    assert conn in digest


def test_digest_keeps_symbol_via_guard():
    sym = "判断条件：a && b，且 c != d"
    digest = build_routing_digest(LONG_FILLER + sym + "\n\n" + LONG_FILLER)
    assert sym in digest


def test_picker_segments_verbatim_validated():
    real = "这一段是真实的 MySQL 主从延迟分析"
    digest = build_routing_digest(
        LONG_FILLER + real + "\n\n" + LONG_FILLER,
        picked_segments=["模型幻觉出来的段落"],
    )
    assert "幻觉出来的段落" not in digest
    assert real in digest


def test_picker_segments_used_with_guards():
    real = "这一段是真实的 MySQL 主从延迟分析"
    neg = "不要直接重启主库"
    digest = build_routing_digest(
        LONG_FILLER + real + "\n\n" + neg,
        picked_segments=[real],
    )
    assert real in digest
    assert neg in digest


def test_digest_includes_best_segment():
    mid = "这段讲 Pod 频繁重启 CrashLoopBackOff"
    digest = build_routing_digest(LONG_FILLER + mid + "\n\n" + LONG_FILLER, best_segment=mid)
    assert mid in digest


def test_digest_detailed_reports_fidelity_forced():
    from app.layers.text_analysis import build_routing_digest_detailed

    neg = "这段明确说不要用这个方案，因为风险太高"
    digest, forced = build_routing_digest_detailed(LONG_FILLER + neg + "\n\n" + LONG_FILLER)
    assert neg in digest
    assert forced >= 1
