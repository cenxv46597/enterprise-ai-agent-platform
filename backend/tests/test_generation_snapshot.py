"""请求快照的公开资料边界与内容指纹；不调用真实模型。"""

from copy import deepcopy
import json

import pytest

from app.providers.generation_snapshot import GenerationSnapshotError, capture_generation_request, digest


PUBLIC = "先暂停导入。健康检查通过后恢复导入。"
QUESTION = "如何恢复导入？"


def payload():
    return {
        "model": "受控测试模型", "stream": False,
        "response_format": {"type": "json_object"}, "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": "只使用给定资料，输出 JSON。"},
            {"role": "user", "content": json.dumps({"question": QUESTION, "sources": [
                {"source_id": 1, "content": "先暂停导入。"},
                {"source_id": 2, "content": "健康检查通过后恢复导入。"},
            ]}, ensure_ascii=False)},
        ],
    }


def test_snapshot_copies_input_and_binds_prompt_parameters_and_source_order():
    body = payload()
    snapshot = capture_generation_request(body, PUBLIC, " " + QUESTION + " ")
    assert snapshot["source_count"] == 2
    assert snapshot["context_characters"] == len(PUBLIC)
    assert snapshot["request_body"] == body
    assert snapshot["request_body_sha256"] == digest(body)
    assert "请求发送前" in snapshot["capture_stage"]
    assert "answer_response_received" not in snapshot
    body["messages"][0]["content"] = "修改提示词"
    assert snapshot["request_body"]["messages"][0]["content"] != "修改提示词"
    assert digest(body) != snapshot["request_body_sha256"]

    changed = payload()
    data = json.loads(changed["messages"][1]["content"])
    data["sources"].reverse()
    for index, source in enumerate(data["sources"], 1):
        source["source_id"] = index
    changed["messages"][1]["content"] = json.dumps(data, ensure_ascii=False)
    reordered = capture_generation_request(changed, PUBLIC, QUESTION)
    assert reordered["input_sha256"] != snapshot["input_sha256"]
    changed["max_tokens"] = 3000
    assert capture_generation_request(changed, PUBLIC, QUESTION)["request_body_sha256"] != reordered["request_body_sha256"]


@pytest.mark.parametrize("change", [
    "authorization", "private_source", "wrong_question", "non_sequential_ids",
    "boolean_id", "empty_sources", "extra_input_field", "invalid_json",
    "boolean_tokens", "stream", "wrong_role", "extra_message_field",
])
def test_rejects_untrusted_body_without_echoing_its_contents(change):
    body = deepcopy(payload())
    data = json.loads(body["messages"][1]["content"])
    private = "PRIVATE-MUST-NOT-BE-SAVED"
    if change == "authorization":
        body["Authorization"] = private
    elif change == "private_source":
        data["sources"][0]["content"] = private
    elif change == "wrong_question":
        data["question"] = private
    elif change == "non_sequential_ids":
        data["sources"][0]["source_id"] = 2
    elif change == "boolean_id":
        data["sources"][0]["source_id"] = True
    elif change == "empty_sources":
        data["sources"] = []
    elif change == "extra_input_field":
        data["api_key"] = private
    elif change == "boolean_tokens":
        body["max_tokens"] = True
    elif change == "stream":
        body["stream"] = True
    elif change == "wrong_role":
        body["messages"][0]["role"] = "user"
    elif change == "extra_message_field":
        body["messages"][0]["token"] = private
    body["messages"][1]["content"] = private if change == "invalid_json" else json.dumps(data)
    with pytest.raises(GenerationSnapshotError) as caught:
        capture_generation_request(body, PUBLIC, QUESTION)
    assert private not in str(caught.value)
