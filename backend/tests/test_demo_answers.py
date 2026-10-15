"""问答演示的 HTTP、引用归属与输出边界；不调用数据库或真实模型。"""

import json
from uuid import UUID

import pytest

from test_browse_demo import (
    BASES, DOCUMENT, DOCUMENTS, KB_ONE, KB_TWO, LOGIN, ME, PASSWORD, TOKEN, WORKSPACES,
    browse, safe_output,
)


ANSWERS = f"{BASES}/{KB_TWO}/answers"
ARGS = ["--username", "demo_a", "--ask"]


@pytest.fixture
def answer_demo(browse):
    browse.payloads[DOCUMENT]["status"] = "ready"
    return browse


def answer_requests(context):
    return [request for request in context.requests if request.method == "POST" and request.url.path == ANSWERS]


def assert_answer_not_shown(context, output: str) -> None:
    assert "回答：" not in output and "引用 1" not in output
    answer = context.payloads[ANSWERS]["answer"]
    if isinstance(answer, str) and answer.strip():
        assert answer not in output
    assert all(citation["quote"] not in output for citation in context.payloads[ANSWERS]["citations"])


def test_ask_uses_selected_scope_fixed_parameters_and_answer_timeout(answer_demo, capsys) -> None:
    question = "导出清单中的一个条目包含哪些信息？"
    answer_demo.question = f"  {question}\n"
    assert answer_demo.module.main(ARGS) == 0
    assert [(request.method, request.url.path) for request in answer_demo.requests] == [
        ("POST", LOGIN), ("GET", ME), ("GET", WORKSPACES), ("GET", BASES),
        ("POST", ANSWERS), ("GET", DOCUMENT),
    ]
    request, = answer_requests(answer_demo)
    assert json.loads(request.content) == {
        "question": question, "top_k": 3, "strategy": "vector", "rerank": False,
    }
    assert request.url.query == b""
    assert request.extensions["timeout"] == {"connect": 10, "read": 630, "write": 10, "pool": 10}
    for other in answer_demo.requests:
        if other is not request:
            assert other.extensions["timeout"] == {"connect": 10, "read": 10, "write": 10, "pool": 10}
    assert "Authorization" not in answer_demo.requests[0].headers
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in answer_demo.requests[1:])
    assert all(dict(request.url.params) == {"limit": "20", "offset": "0"} for request in answer_demo.requests[2:4])
    assert answer_demo.input_calls == 3
    output = safe_output(capsys)
    citation, = answer_demo.payloads[ANSWERS]["citations"]
    assert "回答：" in output and answer_demo.payloads[ANSWERS]["answer"] in output
    assert "引用 1" in output and citation["document_name"] in output and citation["quote"] in output
    assert f"[{citation['start_char']}, {citation['end_char']})" in output
    assert "客户端已逐字回验" not in output


def test_two_different_quotes_from_same_chunk_are_allowed_and_document_is_checked_once(answer_demo, capsys) -> None:
    first, = answer_demo.payloads[ANSWERS]["citations"]
    second = {**first, "quote": first["quote"][:5], "end_char": first["start_char"] + 5}
    answer_demo.payloads[ANSWERS]["citations"].append(second)
    assert answer_demo.module.main(ARGS) == 0
    assert len(answer_demo.requests) == 6 and len(answer_requests(answer_demo)) == 1
    assert sum(request.url.path == DOCUMENT for request in answer_demo.requests) == 1
    output = safe_output(capsys)
    assert "引用 1" in output and "引用 2" in output
    assert first["quote"] in output and second["quote"] in output


def test_empty_library_abstention_is_normal_without_document_reads(answer_demo, capsys) -> None:
    answer_demo.payloads[ANSWERS].update(answer="没有可用证据。", abstained=True, citations=[])
    assert answer_demo.module.main(ARGS) == 0
    assert len(answer_demo.requests) == 5 and len(answer_requests(answer_demo)) == 1
    assert not any(request.url.path.startswith(DOCUMENTS) for request in answer_demo.requests)
    output = safe_output(capsys)
    assert "证据不足，本次弃答" in output and "引用 1" not in output


@pytest.mark.parametrize("question", [" \t\n ", "问" * 2001], ids=["blank", "too-long"])
def test_invalid_question_is_rejected_before_answer_request(answer_demo, capsys, question) -> None:
    answer_demo.question = question
    assert answer_demo.module.main(ARGS) == 1
    assert len(answer_demo.requests) == 4 and answer_requests(answer_demo) == []
    assert_answer_not_shown(answer_demo, safe_output(capsys))


@pytest.mark.parametrize(
    "problem",
    ["empty-answer", "non-bool-abstained", "answer-without-citations", "abstention-with-citations", "wrong-range", "duplicate-quote"],
)
def test_invalid_answer_or_citation_protocol_is_not_displayed(answer_demo, capsys, problem) -> None:
    payload = answer_demo.payloads[ANSWERS]
    if problem == "empty-answer":
        payload["answer"] = " \t "
    elif problem == "non-bool-abstained":
        payload["abstained"] = 1
    elif problem == "answer-without-citations":
        payload["citations"] = []
    elif problem == "abstention-with-citations":
        payload["abstained"] = True
    elif problem == "wrong-range":
        payload["citations"][0]["end_char"] += 1
    else:
        payload["citations"].append(payload["citations"][0].copy())
    assert answer_demo.module.main(ARGS) == 1
    assert len(answer_demo.requests) == 5 and len(answer_requests(answer_demo)) == 1
    output = safe_output(capsys)
    assert "问答结果未确认" in output
    assert_answer_not_shown(answer_demo, output)


@pytest.mark.parametrize("problem", ["second-document-parent", "not-ready"])
def test_all_sources_must_be_verified_before_any_answer_or_quote_is_shown(answer_demo, capsys, problem) -> None:
    if problem == "second-document-parent":
        first, = answer_demo.payloads[ANSWERS]["citations"]
        second_id, quote = str(UUID(int=103)), "第二份文档的引用内容。"
        answer_demo.payloads[ANSWERS]["citations"].append({
            **first, "chunk_id": str(UUID(int=701)), "document_id": second_id,
            "document_name": "资料3.txt", "quote": quote, "start_char": 0, "end_char": len(quote),
        })
        answer_demo.payloads[f"{DOCUMENTS}/{second_id}"] = {
            "id": second_id, "knowledge_base_id": KB_ONE,
            "original_filename": "资料3.txt", "status": "ready",
        }
    else:
        answer_demo.payloads[DOCUMENT]["status"] = "parsed"
    assert answer_demo.module.main(ARGS) == 1
    assert len(answer_demo.requests) == (7 if problem == "second-document-parent" else 6)
    assert len(answer_requests(answer_demo)) == 1
    output = safe_output(capsys)
    assert "问答结果未确认" in output
    assert_answer_not_shown(answer_demo, output)


def test_answer_and_full_quote_escape_controls_and_mask_credentials_without_search_truncation(answer_demo, capsys) -> None:
    filename = f"资料{PASSWORD}\n{TOKEN}\x1b.txt"
    answer_demo.payloads[DOCUMENT]["original_filename"] = filename
    payload = answer_demo.payloads[ANSWERS]
    payload["answer"] = f"公开回答\n{PASSWORD}\x1b{TOKEN}"
    citation, = payload["citations"]
    citation.update(document_name=filename, quote="甲" * 310 + f"\n{PASSWORD}\x1b{TOKEN}" + "FULL-QUOTE-TAIL")
    citation["end_char"] = citation["start_char"] + len(citation["quote"])
    assert answer_demo.module.main(ARGS) == 0
    output = safe_output(capsys)
    assert "回答：" in output and "引用 1" in output
    assert "[已隐藏]" in output and "\\n" in output and "\\u001b" in output
    assert "公开回答\n" not in output
    masked_quote = citation["quote"].replace(PASSWORD, "[已隐藏]").replace(TOKEN, "[已隐藏]")
    assert json.dumps(masked_quote, ensure_ascii=False) in output
    assert "FULL-QUOTE-TAIL" in output


@pytest.mark.parametrize("failure", ["timeout", "network", "cancel", "server", "conflict"])
def test_answer_failures_never_retry_or_show_unconfirmed_text(answer_demo, capsys, failure) -> None:
    answer_demo.answer_failure = failure
    assert answer_demo.module.main(ARGS) == 1
    assert len(answer_demo.requests) == 5 and len(answer_requests(answer_demo)) == 1
    output = safe_output(capsys)
    expected_message = "问答请求被拒绝" if failure == "conflict" else "问答结果未确认"
    assert expected_message in output
    if failure != "conflict":
        assert "未自动重试" in output
    assert_answer_not_shown(answer_demo, output)


@pytest.mark.parametrize("scope,request_count", [(WORKSPACES, 3), (BASES, 4)])
def test_ask_with_no_selectable_scope_makes_no_answer_request(answer_demo, capsys, scope, request_count) -> None:
    answer_demo.payloads[scope] = []
    assert answer_demo.module.main(ARGS) == 1
    assert len(answer_demo.requests) == request_count and answer_requests(answer_demo) == []
    output = safe_output(capsys)
    assert "未提交问答" in output
    assert_answer_not_shown(answer_demo, output)


def test_ask_rejects_other_actions_or_embedding_budget_before_password_or_network(answer_demo, capsys) -> None:
    for incompatible in (
        ["--upload", f"{PASSWORD}.txt"], ["--chunk"], ["--search"],
        ["--embed", "--max-chunks", "3"], ["--max-chunks", "3"],
    ):
        try:
            result = answer_demo.module.main([*ARGS, *incompatible])
        except SystemExit as error:
            result = error.code
        assert result == 2
        assert answer_demo.password_calls == 0 and answer_demo.requests == [] and answer_demo.client_options == []
        safe_output(capsys)
