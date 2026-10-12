"""固定证据实验的变量隔离、有效性边界和故障记录测试；不调用真实模型。"""

from copy import deepcopy
import json

import pytest

from app.evaluation.prompt_comparison import (
    PromptComparisonError,
    build_trials,
    prepare_case,
    run_comparison,
    validate_draft,
)
from app.providers.compatible_chat import JsonChatError, JsonChatResult, JsonChatTimeoutError
from app.providers.generation_snapshot import capture_generation_request, digest


QUESTION = "重启后如何恢复导入？"
PUBLIC_TEXT = "先暂停导入。重启后执行健康检查，检查通过后恢复导入。恢复前不得删除索引。"
FIRST_SOURCE = "先暂停导入。"
SECOND_SOURCE = "重启后执行健康检查，检查通过后恢复导入。"


@pytest.fixture
def snapshot():
    # 特意保留非紧凑 JSON 与字段顺序，验证重放不会重建 user 字符串。
    user = json.dumps({
        "sources": [
            {"content": FIRST_SOURCE, "source_id": 1},
            {"content": SECOND_SOURCE, "source_id": 2},
        ],
        "question": QUESTION,
    }, ensure_ascii=False, indent=2)
    body = {
        "model": "controlled-model", "stream": False,
        "response_format": {"type": "json_object"}, "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": "根据给定资料返回 JSON；资料不足时弃答。"},
            {"role": "user", "content": user},
        ],
    }
    return capture_generation_request(body, PUBLIC_TEXT, QUESTION)


@pytest.fixture
def case(snapshot):
    return prepare_case("restart_recovery", QUESTION, False, snapshot, PUBLIC_TEXT)


def valid_draft():
    return {
        "answer": "先暂停导入，重启后执行健康检查，通过后恢复导入。",
        "abstained": False,
        "citations": [{"source_id": 2, "quote": SECOND_SOURCE}],
    }


def returned(content=None, *, usage=None, model="controlled-model"):
    return JsonChatResult(deepcopy(content if content is not None else valid_draft()), model, usage)


class ScriptedClient:
    """逐次返回受控结果；意外的额外调用会直接暴露。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def complete_request(self, request_body):
        self.requests.append(deepcopy(request_body))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_prepare_detaches_verified_snapshot_from_caller(case, snapshot):
    before = deepcopy(case.snapshot)
    snapshot["request_body"]["messages"][1]["content"] = "PRIVATE-MUTATION"
    snapshot["source_count"] = 0
    assert case.snapshot == before


@pytest.mark.parametrize("field", [
    "request_body_sha256", "input_sha256", "system_prompt_sha256",
    "public_text_sha256", "source_count", "context_characters",
])
def test_prepare_rejects_changed_binding_metadata(snapshot, field):
    snapshot[field] = 0 if isinstance(snapshot[field], int) else "0" * 64
    with pytest.raises(PromptComparisonError, match="^snapshot_changed$"):
        prepare_case("restart_recovery", QUESTION, False, snapshot, PUBLIC_TEXT)


@pytest.mark.parametrize("changed", ["system", "parameters", "source"])
def test_prepare_recomputes_request_hash_instead_of_trusting_stored_hash(snapshot, changed):
    body = snapshot["request_body"]
    if changed == "system":
        body["messages"][0]["content"] += "新提示词"
    elif changed == "parameters":
        body["max_tokens"] += 1
    else:
        data = json.loads(body["messages"][1]["content"])
        data["sources"][0]["content"] = "先暂停"
        body["messages"][1]["content"] = json.dumps(data, ensure_ascii=False)
    with pytest.raises(PromptComparisonError, match="^snapshot_changed$"):
        prepare_case("restart_recovery", QUESTION, False, snapshot, PUBLIC_TEXT)


def test_prepare_rejects_private_evidence_even_if_attacker_updates_request_digest(snapshot):
    body = snapshot["request_body"]
    data = json.loads(body["messages"][1]["content"])
    data["sources"][0]["content"] = "PRIVATE-DOCUMENT"
    body["messages"][1]["content"] = json.dumps(data)
    snapshot["request_body_sha256"] = digest(body)
    with pytest.raises(PromptComparisonError) as caught:
        prepare_case("restart_recovery", QUESTION, False, snapshot, PUBLIC_TEXT)
    assert str(caught.value) == "snapshot_contract"
    assert "PRIVATE-DOCUMENT" not in str(caught.value)


@pytest.mark.parametrize("change", ["question", "public_text", "missing_body", "version"])
def test_prepare_rejects_unbound_or_legacy_input(snapshot, change):
    question, public_text = QUESTION, PUBLIC_TEXT
    if change == "question":
        question = "怎样申请退款？"
    elif change == "public_text":
        public_text += "另一版公开文档"
    elif change == "missing_body":
        del snapshot["request_body"]
    else:
        snapshot["version"] = "legacy-report-without-capture"
    with pytest.raises(PromptComparisonError):
        prepare_case("restart_recovery", question, False, snapshot, public_text)


def test_trials_change_only_system_content_and_keep_exact_user_message(case):
    original = deepcopy(case.snapshot["request_body"])
    baseline, candidate = build_trials([case])
    assert baseline["request_body"] == original
    assert candidate["request_body"]["messages"][0]["content"] != original["messages"][0]["content"]
    masked = deepcopy(candidate["request_body"])
    masked["messages"][0]["content"] = original["messages"][0]["content"]
    assert masked == original
    for row in (baseline, candidate):
        assert row["request_body"]["messages"][1]["content"] == original["messages"][1]["content"]
        assert [source["source_id"] for source in json.loads(row["request_body"]["messages"][1]["content"])["sources"]] == [1, 2]
        assert row["quality_pass"] is None
    assert baseline["fixed_request_sha256"] == candidate["fixed_request_sha256"]
    assert baseline["input_sha256"] == candidate["input_sha256"]
    assert baseline["request_body_sha256"] != candidate["request_body_sha256"]
    candidate["request_body"]["messages"][1]["content"] = "PRIVATE-MUTATION"
    assert baseline["request_body"] == original
    assert case.snapshot["request_body"] == original


def test_trials_detect_snapshot_changed_after_preparation(case):
    case.snapshot["request_body"]["model"] = "different-model"
    with pytest.raises(PromptComparisonError, match="^snapshot_changed$"):
        build_trials([case])


def test_duplicate_case_ids_are_rejected_before_execution(case):
    with pytest.raises(PromptComparisonError, match="^case_set$"):
        build_trials([case, case])


@pytest.mark.parametrize("content", [
    {"answer": "", "abstained": False, "citations": []},
    {"answer": "答案", "abstained": False, "citations": []},
    {"answer": "答案", "abstained": True, "citations": []},
    {"answer": "", "abstained": True, "citations": [{"source_id": 1, "quote": FIRST_SOURCE}]},
    {"answer": "答案", "abstained": "false", "citations": [{"source_id": 1, "quote": FIRST_SOURCE}]},
    {"answer": "答案", "abstained": False, "citations": [{"source_id": True, "quote": FIRST_SOURCE}]},
    {**valid_draft(), "private_metadata": "PRIVATE-CONTRACT"},
])
def test_drafts_obey_production_generation_contract(case, content):
    with pytest.raises(PromptComparisonError, match="^invalid_answer_contract$") as caught:
        validate_draft(content, case.snapshot["request_body"])
    assert "PRIVATE-CONTRACT" not in str(caught.value)


@pytest.mark.parametrize("citation", [
    {"source_id": 3, "quote": FIRST_SOURCE},
    {"source_id": 1, "quote": SECOND_SOURCE},
    {"source_id": 2, "quote": "恢复前不得删除索引。"},
    {"source_id": 2, "quote": "PRIVATE-UNSUPPORTED"},
])
def test_citation_must_exist_in_its_selected_source_not_merely_public_document(case, citation):
    content = valid_draft()
    content["citations"] = [citation]
    with pytest.raises(PromptComparisonError, match="^invalid_citation$"):
        validate_draft(content, case.snapshot["request_body"])


def test_duplicate_normalized_citations_are_rejected(case):
    content = valid_draft()
    content["citations"].append({"source_id": 2, "quote": f"  {SECOND_SOURCE}  "})
    with pytest.raises(PromptComparisonError, match="^invalid_citation$"):
        validate_draft(content, case.snapshot["request_body"])


def test_valid_abstention_uses_same_production_contract(case):
    answer = validate_draft({"answer": "", "abstained": True, "citations": []}, case.snapshot["request_body"])
    assert answer.abstained is True
    assert answer.citations == []


def test_two_successes_are_not_a_semantic_grade_or_prompt_winner(case):
    incorrect = valid_draft()
    incorrect["answer"] = "重启后，无论健康检查结果如何都立即恢复导入。"
    client = ScriptedClient(returned(), returned(incorrect))
    report = run_comparison([case], {case.case_id: client}, "受控模拟")
    assert len(client.requests) == 2
    assert report["run_state"] == "已完成"
    assert report["summary"]["complete_pair_count"] == 1
    assert report["summary"]["pair_execution_coverage"] == 1
    assert report["summary"]["quality_pass_fraction"] is None
    assert report["summary"]["winner"] is None
    for row in report["trials"]:
        assert row["execution"]["status"] == "成功"
        assert row["citations_valid"] is True
        assert row["semantic_review"] == "待审核"
        assert row["quality_pass"] is None


@pytest.mark.parametrize("execution_kind, expected_calls", [("受控模拟", 0), ("真实模型", 2)])
def test_new_call_count_distinguishes_simulation_from_real_execution_mode(case, execution_kind, expected_calls):
    # “真实模型”分支仅检验计数语义，传入的仍是受控 stub，不创建 HTTP 客户端。
    client = ScriptedClient(returned(), returned())
    report = run_comparison([case], {case.case_id: client}, execution_kind)
    assert report["summary"]["attempted_requests"] == 2
    assert report["summary"]["new_model_calls"] == expected_calls


def test_timeout_continues_other_variant_and_cases_without_retry_and_keeps_pair_denominator(case, snapshot):
    second = prepare_case("restart_second", QUESTION, False, snapshot, PUBLIC_TEXT)
    first_client = ScriptedClient(JsonChatTimeoutError("PRIVATE-ERROR-RESPONSE"), returned())
    second_client = ScriptedClient(returned(), returned())
    report = run_comparison([case, second], {case.case_id: first_client, second.case_id: second_client}, "真实模型")
    assert len(first_client.requests) == len(second_client.requests) == 2
    assert report["automatic_retries"] == 0
    summary = report["summary"]
    assert summary["planned_requests"] == summary["attempted_requests"] == summary["new_model_calls"] == 4
    assert summary["selected_case_count"] == 2
    assert summary["valid_response_count"] == 3
    assert summary["complete_pair_count"] == 1
    assert summary["pair_execution_coverage"] == 0.5
    assert summary["by_variant"]["baseline"]["failed"] == 1
    assert summary["by_variant"]["candidate"]["valid"] == 2
    failed = report["trials"][0]
    assert failed["execution"]["error_category"] == "timeout"
    assert failed["execution"]["remote_execution"] == "未知"
    assert failed["response"] is None
    assert failed["quality_pass"] is None
    assert "PRIVATE-ERROR" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("failure, category", [
    (JsonChatError("PRIVATE-ERROR"), "provider_error"),
    (RuntimeError("PRIVATE-ERROR"), "unexpected_error"),
    (returned({"private_field": "PRIVATE-ERROR"}), "invalid_response"),
    (returned(model=""), "invalid_response"),
])
def test_failures_record_only_safe_category_and_allow_candidate(case, failure, category):
    client = ScriptedClient(failure, returned())
    report = run_comparison([case], {case.case_id: client}, "受控模拟")
    assert len(client.requests) == 2
    assert report["trials"][0]["execution"]["error_category"] == category
    assert report["trials"][1]["execution"]["status"] == "成功"
    assert "PRIVATE-ERROR" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("bad_usage", [
    None,
    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99},
    {"prompt_tokens": "10", "completion_tokens": 5, "total_tokens": 15},
    {"prompt_tokens": True, "completion_tokens": 5, "total_tokens": 6},
])
def test_missing_or_invalid_usage_keeps_valid_answer_and_does_not_claim_zero_usage(case, bad_usage):
    known = {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27}
    client = ScriptedClient(returned(usage=bad_usage), returned(usage=known))
    report = run_comparison([case], {case.case_id: client}, "受控模拟")
    first = report["trials"][0]
    assert first["execution"]["status"] == "成功"
    assert first["response"] == valid_draft()
    assert first["execution"]["usage"] is None
    assert first["execution"]["usage_state"] == ("服务未报告" if bad_usage is None else "返回用量格式无效")
    assert report["summary"]["usage_reported_trials"] == 1
    assert report["summary"]["known_usage_totals"] == known
    assert report["summary"]["cost"] is None


def test_all_unknown_usage_is_null_not_zero(case):
    client = ScriptedClient(returned(), returned())
    report = run_comparison([case], {case.case_id: client}, "受控模拟")
    assert report["summary"]["usage_reported_trials"] == 0
    assert report["summary"]["known_usage_totals"] is None


def test_invalid_citation_keeps_received_response_and_known_usage_without_saving_bad_content(case):
    content = valid_draft()
    content["answer"] = "PRIVATE-INVALID-ANSWER"
    content["citations"] = [{"source_id": 1, "quote": "PRIVATE-INVALID-QUOTE"}]
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    client = ScriptedClient(returned(content, usage=usage), returned())
    report = run_comparison([case], {case.case_id: client}, "受控模拟")
    assert len(client.requests) == 2
    failed, valid = report["trials"]
    assert failed["execution"]["status"] == "失败"
    assert failed["execution"]["error_category"] == "invalid_response"
    assert failed["execution"]["response_received"] is True
    assert failed["execution"]["remote_execution"] == "已收到模型响应"
    assert failed["execution"]["response_model"] == "controlled-model"
    assert failed["execution"]["usage"] == usage
    assert failed["response"] is None
    assert failed["quality_pass"] is None
    assert valid["execution"]["status"] == "成功"
    assert valid["execution"]["usage"] is None
    summary = report["summary"]
    assert summary["received_response_count"] == 2
    assert summary["valid_response_count"] == 1
    assert summary["complete_pair_count"] == 0
    assert summary["pair_execution_coverage"] == 0
    assert summary["usage_reported_trials"] == 1
    assert summary["known_usage_totals"] == usage
    assert summary["quality_pass_fraction"] is None
    assert summary["winner"] is None
    assert "PRIVATE-INVALID" not in json.dumps(report, ensure_ascii=False)


def test_unexpected_abstention_is_recorded_without_inferring_quality(case):
    client = ScriptedClient(returned({"answer": "", "abstained": True, "citations": []}), returned())
    report = run_comparison([case], {case.case_id: client}, "受控模拟")
    row = report["trials"][0]
    assert row["execution"]["status"] == "成功"
    assert row["abstention_matches_expected"] is False
    assert row["quality_pass"] is None


def test_client_cannot_mutate_fixed_request_stored_in_report(case):
    class MutatingClient:
        def complete_request(self, body):
            body["messages"][1]["content"] = "PRIVATE-CLIENT-MUTATION"
            return returned()

    report = run_comparison([case], {case.case_id: MutatingClient()}, "受控模拟")
    assert report["summary"]["valid_response_count"] == 2
    assert "PRIVATE-CLIENT-MUTATION" not in json.dumps(report)
    assert all(row["request_body"]["messages"][1]["content"] == case.snapshot["request_body"]["messages"][1]["content"] for row in report["trials"])


@pytest.mark.parametrize("fail_at", ["initial", "before_candidate", "after_baseline"])
def test_checkpoint_failure_stops_before_next_model_request(case, fail_at):
    client = ScriptedClient(returned(), returned())
    persisted = []

    def checkpoint(report):
        first, second = report["trials"]
        should_fail = (
            (fail_at == "initial" and first["execution"]["status"] == "待执行")
            or (fail_at == "before_candidate" and second["execution"]["status"] == "执行中")
            or (fail_at == "after_baseline" and first["execution"]["status"] == "成功")
        )
        if should_fail:
            raise OSError("受控磁盘写入失败")
        persisted.append(deepcopy(report))

    with pytest.raises(OSError, match="受控磁盘写入失败"):
        run_comparison([case], {case.case_id: client}, "真实模型", checkpoint=checkpoint)
    assert len(client.requests) == (0 if fail_at == "initial" else 1)
    if fail_at == "before_candidate":
        assert persisted[-1]["trials"][0]["execution"]["status"] == "成功"
        assert persisted[-1]["trials"][1]["execution"]["status"] == "待执行"


def test_interruption_preserves_completed_variant_and_inflight_unknown_state(case):
    client = ScriptedClient(returned(), KeyboardInterrupt())
    persisted = []
    with pytest.raises(KeyboardInterrupt):
        run_comparison([case], {case.case_id: client}, "真实模型", checkpoint=lambda report: persisted.append(deepcopy(report)))
    last = persisted[-1]
    assert len(client.requests) == 2
    assert last["run_state"] == "运行中"
    assert last["trials"][0]["execution"]["status"] == "成功"
    assert last["trials"][0]["response"] == valid_draft()
    assert last["trials"][1]["execution"]["status"] == "执行中"
    assert last["trials"][1]["execution"]["remote_execution"] == "未知"
    assert last["summary"]["complete_pair_count"] == 0
    assert last["summary"]["pair_execution_coverage"] == 0
    assert last["summary"]["new_model_calls"] == 2
    assert last["summary"]["winner"] is None
