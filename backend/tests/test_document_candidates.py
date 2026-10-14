"""文件名候选查询的范围、字面匹配、有界结果与只读边界；不调用模型。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import Document
from app.knowledge.document_candidates import MAX_DOCUMENT_CANDIDATES, find_document_candidates
# 复用独立 eaap_test 随机 schema 三表 fixture。
from test_document_status import status_db  # noqa: F401


def add_candidates(context: SimpleNamespace, filenames: list[str], *, kb_id=None, same_time=False):
    identifiers = sorted([uuid4() for _ in filenames], reverse=True)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(context.engine) as session, session.begin():
        for index, (identifier, filename) in enumerate(zip(identifiers, filenames)):
            session.add(Document(
                id=identifier, knowledge_base_id=kb_id or context.kb_a, original_filename=filename,
                media_type="text/plain", storage_key=f"private/{identifier}", byte_size=10 + index,
                sha256="b" * 64, status="parsed",
                created_at=start + timedelta(seconds=0 if same_time else index),
            ))
    return identifiers


def test_candidates_include_documents_that_are_not_ready(status_db: SimpleNamespace) -> None:
    with Session(status_db.engine) as session:
        result = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "同名资料")
    assert {item.document_id for item in result.candidates} == set(status_db.documents.values())
    assert result.has_more is False
    for item in result.candidates:
        assert set(item.model_dump()) == {"document_id", "original_filename", "created_at", "byte_size"}


@pytest.mark.parametrize("case", ["other_workspace", "same_workspace_other_kb", "mismatched_scope"])
def test_candidates_stay_in_bound_workspace_and_knowledge_base(status_db: SimpleNamespace, case: str) -> None:
    own = add_candidates(status_db, ["范围线索.txt"])[0]
    same_workspace = add_candidates(status_db, ["范围线索.txt"], kb_id=status_db.kb_a_second)[0]
    other_workspace = add_candidates(status_db, ["范围线索.txt"], kb_id=status_db.kb_b)[0]
    with Session(status_db.engine) as session:
        result = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "范围线索")
        assert [item.document_id for item in result.candidates] == [own]
        if case == "other_workspace":
            workspace_id, kb_id, expected = status_db.workspace_b, status_db.kb_b, [other_workspace]
        elif case == "same_workspace_other_kb":
            workspace_id, kb_id, expected = status_db.workspace_a, status_db.kb_a_second, [same_workspace]
        else:
            workspace_id, kb_id, expected = status_db.workspace_b, status_db.kb_a, []
        result = find_document_candidates(session, workspace_id, kb_id, "范围线索")
    assert [item.document_id for item in result.candidates] == expected
    assert result.has_more is False


def test_same_name_returns_both_uploads_for_selection(status_db: SimpleNamespace) -> None:
    identifiers = add_candidates(status_db, ["两次上传.txt", "两次上传.txt"])
    with Session(status_db.engine) as session:
        result = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "两次上传")
    assert [item.document_id for item in result.candidates] == identifiers
    first, second = result.candidates
    assert first.original_filename == second.original_filename == "两次上传.txt"
    assert first.created_at < second.created_at
    assert (first.byte_size, second.byte_size) == (10, 11)
    assert result.has_more is False


@pytest.mark.parametrize("filename,decoy,query", [
    ("预算100%.txt", "预算1000.txt", "100%"),
    ("risk_plan.txt", "riskXplan.txt", "risk_plan"),
    ("a/b.txt", "ab.txt", "a/b"),
    ("director's.txt", "directors.txt", "'"),
    ("REPORT_FINAL.TXT", "NOTES.TXT", " report_final "),
    ("x" * 255, "x" * 254, "x" * 255),
])
def test_filename_clues_are_literal_case_insensitive_substrings(status_db, filename, decoy, query) -> None:
    target, _ = add_candidates(status_db, [filename, decoy])
    with Session(status_db.engine) as session:
        result = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, query)
    assert [item.document_id for item in result.candidates] == [target]
    assert result.has_more is False


@pytest.mark.parametrize("query", ["", "   ", "x" * 256, "资料\0.txt", 42])
def test_invalid_filename_clues_are_rejected(status_db: SimpleNamespace, query) -> None:
    with Session(status_db.engine) as session:
        with pytest.raises(ValidationError):
            find_document_candidates(session, status_db.workspace_a, status_db.kb_a, query)


def test_no_matching_document_returns_an_empty_candidate_list(status_db: SimpleNamespace) -> None:
    with Session(status_db.engine) as session:
        result = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "不存在的资料")
    assert result.candidates == ()
    assert result.has_more is False


@pytest.mark.parametrize("count", [4, 5, 6])
def test_limit_and_timestamp_ties_are_stable(status_db: SimpleNamespace, count: int) -> None:
    identifiers = add_candidates(status_db, [f"候选上限{index}.txt" for index in range(count)], same_time=True)
    with Session(status_db.engine) as session:
        first = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "候选上限")
        second = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "候选上限")
    assert MAX_DOCUMENT_CANDIDATES == 5
    assert [item.document_id for item in first.candidates] == sorted(identifiers)[:5]
    assert len(first.candidates) == min(count, MAX_DOCUMENT_CANDIDATES)
    assert first.has_more is (count > 5)
    assert first == second


def test_dirty_session_does_not_flush_or_expose_storage_paths(status_db: SimpleNamespace) -> None:
    statements = []

    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.split(None, 1)[0].upper())

    with Session(status_db.engine) as session:
        document = session.get(Document, status_db.documents["ready"])
        document.original_filename = "尚未提交的名字.txt"
        document.byte_size = 999
        document.storage_key = "private/uncommitted/path"
        event.listen(status_db.engine, "before_cursor_execute", record_statement)
        try:
            result = find_document_candidates(session, status_db.workspace_a, status_db.kb_a, "同名资料")
            candidate = next(item for item in result.candidates if item.document_id == document.id)
            assert (candidate.original_filename, candidate.byte_size) == ("同名资料.txt", 1)
            assert set(candidate.model_dump()) == {"document_id", "original_filename", "created_at", "byte_size"}
            assert document in session.dirty
            assert session.in_transaction()
            assert statements and all(statement == "SELECT" for statement in statements)
        finally:
            event.remove(status_db.engine, "before_cursor_execute", record_statement)
            session.rollback()
