"""独立检索演示只使用 MockTransport，正文需在归属与状态复核后展示。"""

import json
from uuid import UUID

import pytest

from test_browse_demo import (
    BASES, DOCUMENT, DOCUMENTS, KB_ONE, KB_TWO, LOGIN, ME, PASSWORD, TOKEN, WORKSPACES,
    browse, safe_output,
)


SEARCH = f"{BASES}/{KB_TWO}/search"
ARGS = ["--username", "demo_a", "--search"]


@pytest.fixture
def search_demo(browse):
    browse.payloads[DOCUMENT]["status"] = "ready"
    return browse


def search_requests(context):
    return [request for request in context.requests if request.method == "POST" and request.url.path == SEARCH]


def assert_candidates_not_shown(context, output: str) -> None:
    assert "检索完成" not in output
    assert all(hit["content"] not in output for hit in context.payloads[SEARCH]["results"])
    assert not any("/answers" in request.url.path for request in context.requests)


def test_search_uses_selected_scope_fixed_parameters_and_checks_each_document_once(search_demo, capsys) -> None:
    search_demo.query = "  如何查看导出记录？\n"
    assert search_demo.module.main(ARGS) == 0
    assert [(request.method, request.url.path) for request in search_demo.requests] == [
        ("POST", LOGIN), ("GET", ME), ("GET", WORKSPACES), ("GET", BASES),
        ("POST", SEARCH), ("GET", DOCUMENT),
    ]
    request, = search_requests(search_demo)
    assert json.loads(request.content) == {
        "query": "如何查看导出记录？", "top_k": 3, "strategy": "vector", "rerank": False,
    }
    assert request.url.query == b""
    assert request.extensions["timeout"] == {"connect": 10, "read": 330, "write": 10, "pool": 10}
    for other in search_demo.requests:
        if other is not request:
            assert other.extensions["timeout"] == {"connect": 10, "read": 10, "write": 10, "pool": 10}
    assert "Authorization" not in search_demo.requests[0].headers
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in search_demo.requests[1:])
    assert all(dict(request.url.params) == {"limit": "20", "offset": "0"} for request in search_demo.requests[2:4])
    assert search_demo.input_calls == 3
    output = safe_output(capsys)
    assert "检索完成：返回 2 个候选段落" in output
    for hit in search_demo.payloads[SEARCH]["results"]:
        assert hit["document_name"] in output and hit["content"] in output
        assert f"[{hit['start_char']}, {hit['end_char']})" in output
        assert f"{hit['distance']:.4f}" in output


def test_empty_candidates_are_normal_and_do_not_request_document_details(search_demo, capsys) -> None:
    search_demo.payloads[SEARCH].update(results=[], model=None, dimensions=None)
    assert search_demo.module.main(ARGS) == 0
    assert len(search_demo.requests) == 5 and len(search_requests(search_demo)) == 1
    assert not any(request.url.path.startswith(DOCUMENTS) for request in search_demo.requests)
    assert "候选为空" in safe_output(capsys)


@pytest.mark.parametrize("query", [" \t\n ", "问" * 2001], ids=["blank", "too-long"])
def test_invalid_question_is_rejected_before_search_request(search_demo, capsys, query) -> None:
    search_demo.query = query
    assert search_demo.module.main(ARGS) == 1
    assert len(search_demo.requests) == 4 and search_requests(search_demo) == []
    assert_candidates_not_shown(search_demo, safe_output(capsys))


@pytest.mark.parametrize("field,value", [("metric", "reciprocal_rank_fusion"), ("strategy", "hybrid")])
def test_mismatched_search_protocol_stops_before_document_reads(search_demo, capsys, field, value) -> None:
    search_demo.payloads[SEARCH][field] = value
    assert search_demo.module.main(ARGS) == 1
    assert len(search_demo.requests) == 5 and len(search_requests(search_demo)) == 1
    assert_candidates_not_shown(search_demo, safe_output(capsys))


@pytest.mark.parametrize("problem", ["duplicate-chunk", "wrong-span", "distance-order"])
def test_invalid_candidate_structure_is_not_displayed(search_demo, capsys, problem) -> None:
    first, second = search_demo.payloads[SEARCH]["results"]
    if problem == "duplicate-chunk":
        second["chunk_id"] = first["chunk_id"]
    elif problem == "wrong-span":
        first["end_char"] += 1
    else:
        first["distance"], second["distance"] = 0.2, 0.1
    assert search_demo.module.main(ARGS) == 1
    assert len(search_demo.requests) == 5
    assert_candidates_not_shown(search_demo, safe_output(capsys))


@pytest.mark.parametrize("problem", ["parent", "not-ready", "identity", "filename"])
def test_document_verification_failure_does_not_release_candidate_content(search_demo, capsys, problem) -> None:
    document = search_demo.payloads[DOCUMENT]
    if problem == "parent":
        second_id = str(UUID(int=103))
        second_hit = search_demo.payloads[SEARCH]["results"][1]
        second_hit.update(document_id=second_id, document_name="资料3.txt")
        search_demo.payloads[f"{DOCUMENTS}/{second_id}"] = {
            "id": second_id, "knowledge_base_id": KB_ONE,
            "original_filename": "资料3.txt", "status": "ready",
        }
    elif problem == "not-ready":
        document["status"] = "parsed"
    elif problem == "identity":
        document["id"] = str(UUID(int=999))
    else:
        document["original_filename"] = "另一份资料.txt"
    assert search_demo.module.main(ARGS) == 1
    assert len(search_demo.requests) == (7 if problem == "parent" else 6)
    assert len(search_requests(search_demo)) == 1
    assert_candidates_not_shown(search_demo, safe_output(capsys))


def test_candidate_output_escapes_controls_masks_credentials_and_truncates_content(search_demo, capsys) -> None:
    filename = f"资料{PASSWORD}\n{TOKEN}\x1b.txt"
    search_demo.payloads[DOCUMENT]["original_filename"] = filename
    for hit in search_demo.payloads[SEARCH]["results"]:
        hit["document_name"] = filename
    first = search_demo.payloads[SEARCH]["results"][0]
    first["content"] = f"公开第一行\n{PASSWORD}\x1b{TOKEN}" + "甲" * 400 + "UNSHOWN-TRUNCATED-TAIL"
    first["end_char"] = first["start_char"] + len(first["content"])
    second = search_demo.payloads[SEARCH]["results"][1]
    second["content"] = "乙" * 295 + PASSWORD + "丙" * 30
    second["end_char"] = second["start_char"] + len(second["content"])
    assert search_demo.module.main(ARGS) == 0
    output = safe_output(capsys)
    assert "检索完成：返回 2 个候选段落" in output
    assert "[已隐藏]" in output and "\\n" in output and "\\u001b" in output
    assert "公开第一行\n" not in output
    assert "UNSHOWN-TRUNCATED-TAIL" not in output and "…" in output
    # 密码跨越原始 300 字符边界，仍必须先整体隐藏，不能留下密码前缀。
    expected_second_excerpt = json.dumps("乙" * 295 + "[已隐藏]…", ensure_ascii=False)
    assert f"原文摘录：{expected_second_excerpt}" in output


@pytest.mark.parametrize("failure", ["timeout", "network", "cancel", "server", "conflict"])
def test_search_failure_never_retries_or_generates_an_answer(search_demo, capsys, failure) -> None:
    search_demo.search_failure = failure
    assert search_demo.module.main(ARGS) == 1
    assert len(search_demo.requests) == 5 and len(search_requests(search_demo)) == 1
    assert_candidates_not_shown(search_demo, safe_output(capsys))


def test_search_rejects_other_actions_and_embedding_budget_before_password_or_network(search_demo, capsys) -> None:
    for incompatible in (
        ["--upload", f"{PASSWORD}.txt"], ["--chunk"],
        ["--embed", "--max-chunks", "3"], ["--max-chunks", "3"],
    ):
        try:
            result = search_demo.module.main([*ARGS, *incompatible])
        except SystemExit as error:
            result = error.code
        assert result == 2
        assert search_demo.password_calls == 0 and search_demo.requests == [] and search_demo.client_options == []
        safe_output(capsys)
