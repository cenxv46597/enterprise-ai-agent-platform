"""独立 PostgreSQL 验证显式偏好、版本清除和并发；此处不调用模型。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AnswerPreference, KnowledgeBase
from app.main import create_app
from auth_helpers import copy_auth
from app.memory.contracts import (
    MAX_PREFERENCE_VERSION, PreferenceConflictError, PreferenceRead,
    PreferenceStateError, PreferenceUpdate,
)
from app.memory.service import load_preference, read_preference, set_preference
from test_conversation_migration import load_migration
from test_retrieval import retrieval_context


def url(context, **options):
    return context.url(**options).removesuffix("/search") + "/answer-preferences"


def write(context, detail="concise", version=0, **options):
    return context.client.put(url(context, **options), json={"expected_version": version, "answer_detail": detail})


def test_missing_preference_reads_as_zero_and_does_not_create_a_row(retrieval_context):
    context = retrieval_context
    body = context.client.get(url(context)).json()
    assert body == {"knowledge_base_id": str(context.knowledge_base_id), "version": 0, "answer_detail": None}
    with Session(context.engine) as session:
        assert session.scalar(select(func.count()).select_from(AnswerPreference)) == 0
    assert context.provider.calls == 0


def test_create_update_and_clear_keep_a_monotonic_versioned_row(retrieval_context):
    context = retrieval_context
    first = write(context)
    assert first.status_code == 200 and first.json()["version"] == 1 and first.json()["answer_detail"] == "concise"
    second = write(context, "detailed", 1)
    assert second.status_code == 200 and second.json()["version"] == 2 and second.json()["answer_detail"] == "detailed"
    cleared = write(context, None, 2)
    assert cleared.status_code == 200 and cleared.json()["version"] == 3 and cleared.json()["answer_detail"] is None
    assert context.client.get(url(context)).json() == cleared.json()
    with Session(context.engine) as session:
        row = session.get(AnswerPreference, context.knowledge_base_id)
        assert row.version == 3 and row.answer_detail is None
        assert row.created_at is not None and row.updated_at is not None
        assert session.scalar(select(func.count()).select_from(AnswerPreference)) == 1
    assert context.provider.calls == 0


def test_clear_before_any_setting_also_creates_a_version_tombstone(retrieval_context):
    context = retrieval_context
    cleared = write(context, None)
    assert cleared.status_code == 200 and cleared.json()["version"] == 1 and cleared.json()["answer_detail"] is None
    assert write(context, "concise", 0).status_code == 409
    assert write(context, "detailed", 1).json()["version"] == 2


def test_same_detail_is_still_a_new_version_not_an_implicit_request_replay(retrieval_context):
    context = retrieval_context
    assert write(context).json()["version"] == 1
    assert write(context, "concise", 1).json()["version"] == 2
    assert write(context, "concise", 1).status_code == 409


def test_restart_reads_only_persisted_preference_from_new_application(retrieval_context):
    context = retrieval_context
    saved = write(context, "detailed").json()
    fresh = create_app()
    fresh.dependency_overrides.update(context.client.app.dependency_overrides)
    with TestClient(fresh) as client:
        copy_auth(context.client, client)
        restored = client.get(url(context))
    assert restored.status_code == 200 and restored.json() == saved
    assert context.provider.calls == 0


def test_preference_is_isolated_per_knowledge_base_and_workspace(retrieval_context):
    context = retrieval_context
    other_kb = context.knowledge_base()
    other_workspace = context.workspace()
    foreign_kb = context.knowledge_base(workspace_id=other_workspace)
    assert write(context).status_code == 200
    assert context.client.get(url(context, knowledge_base_id=other_kb)).json()["answer_detail"] is None
    assert write(context, "detailed", knowledge_base_id=other_kb).json()["answer_detail"] == "detailed"
    assert context.client.get(url(context)).json()["answer_detail"] == "concise"
    assert write(context, "detailed", workspace_id=other_workspace, knowledge_base_id=foreign_kb).status_code == 200
    assert context.client.get(url(context, knowledge_base_id=foreign_kb)).status_code == 404


@pytest.mark.parametrize("mismatch", ["workspace", "knowledge_base", "missing_workspace", "missing_knowledge_base"])
def test_scope_mismatch_is_404_for_both_read_and_write_without_mutation(mismatch, retrieval_context):
    context = retrieval_context
    assert write(context).status_code == 200
    options = {"workspace_id": context.workspace()} if mismatch == "workspace" else {}
    if mismatch == "knowledge_base":
        options["knowledge_base_id"] = context.knowledge_base(workspace_id=context.workspace())
    elif mismatch == "missing_workspace":
        options["workspace_id"] = uuid4()
    elif mismatch == "missing_knowledge_base":
        options["knowledge_base_id"] = uuid4()
    assert context.client.get(url(context, **options)).status_code == 404
    assert write(context, "detailed", 1, **options).status_code == 404
    assert context.client.get(url(context)).json()["version"] == 1
    assert context.provider.calls == 0


@pytest.mark.parametrize("body", [
    {}, {"expected_version": 0}, {"answer_detail": "concise"},
    {"expected_version": True, "answer_detail": "concise"},
    {"expected_version": "0", "answer_detail": "concise"},
    {"expected_version": 0.0, "answer_detail": "concise"},
    {"expected_version": -1, "answer_detail": "concise"},
    {"expected_version": MAX_PREFERENCE_VERSION, "answer_detail": "concise"},
    {"expected_version": 0, "answer_detail": "short"},
    {"expected_version": 0, "answer_detail": True},
    {"expected_version": 0, "answer_detail": 1},
    {"expected_version": 0, "answer_detail": "concise", "business_fact": "总是十二个月"},
])
def test_invalid_request_is_rejected_without_saving_free_text_or_a_version(body, retrieval_context):
    context = retrieval_context
    response = context.client.put(url(context), json=body)
    assert response.status_code == 422
    assert context.client.get(url(context)).json()["version"] == 0
    assert context.provider.calls == 0


@pytest.mark.parametrize("version", [0, 2, 99])
def test_old_or_future_version_cannot_replace_existing_preference(version, retrieval_context):
    context = retrieval_context
    original = write(context).json()
    assert write(context, "detailed", version).status_code == 409
    assert context.client.get(url(context)).json() == original


def test_positive_expected_version_cannot_create_an_absent_row(retrieval_context):
    context = retrieval_context
    assert write(context, "detailed", 1).status_code == 409
    assert context.client.get(url(context)).json()["version"] == 0


def test_clear_prevents_old_create_or_update_requests_from_resurrecting_preference(retrieval_context):
    context = retrieval_context
    assert write(context).status_code == 200
    cleared = write(context, None, 1).json()
    assert cleared["version"] == 2
    assert write(context, "detailed", 0).status_code == 409
    assert write(context, "detailed", 1).status_code == 409
    assert context.client.get(url(context)).json() == cleared


@pytest.mark.parametrize("initial", [None, "concise", "cleared"])
def test_concurrent_first_insert_existing_update_or_tombstone_has_one_winner(initial, retrieval_context):
    context = retrieval_context
    version = 0
    if initial is not None:
        write(context)
        version = 1
        if initial == "cleared":
            write(context, None, 1)
            version = 2
    requests = [PreferenceUpdate(expected_version=version, answer_detail=value) for value in ("concise", "detailed")]
    barrier = Barrier(2)
    def execute(request):
        with Session(context.engine) as session:
            barrier.wait(timeout=10)
            try:
                return set_preference(session, context.workspace_id, context.knowledge_base_id, request)
            except PreferenceConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, requests))
    assert results.count("conflict") == 1
    winner = next(item for item in results if item != "conflict")
    assert winner.version == version + 1
    assert context.client.get(url(context)).json() == winner.model_dump(mode="json")
    with Session(context.engine) as session:
        assert session.scalar(select(func.count()).select_from(AnswerPreference)) == 1


def test_load_preference_reuses_existing_transaction_instead_of_starting_one(retrieval_context):
    context = retrieval_context
    saved = write(context).json()
    with Session(context.engine) as session, session.begin():
        assert session.in_transaction()
        result = load_preference(session, context.knowledge_base_id)
        assert result.model_dump(mode="json") == saved and session.in_transaction()


def test_service_read_checks_scope_then_returns_detached_immutable_snapshot(retrieval_context):
    context = retrieval_context
    write(context, "detailed")
    with Session(context.engine) as session:
        result = read_preference(session, context.workspace_id, context.knowledge_base_id)
        assert not session.in_transaction()
    assert result.answer_detail == "detailed"
    with pytest.raises(ValidationError):
        result.answer_detail = "concise"


@pytest.mark.parametrize("values", [{"version": 0}, {"version": -1}, {"version": MAX_PREFERENCE_VERSION + 1},
                                    {"answer_detail": "private-invalid-content"}])
def test_database_constraints_reject_invalid_preference_rows(values, retrieval_context):
    context = retrieval_context
    with Session(context.engine) as session, pytest.raises(DBAPIError):
        with session.begin():
            session.add(AnswerPreference(knowledge_base_id=context.knowledge_base_id,
                                         **{"version": 1, "answer_detail": "concise", **values}))
            session.flush()
    assert context.client.get(url(context)).json()["version"] == 0


def test_database_primary_key_foreign_key_and_restrict_deletion_are_enforced(retrieval_context):
    context = retrieval_context
    assert write(context).status_code == 200
    with Session(context.engine) as session, pytest.raises(IntegrityError):
        with session.begin():
            session.add(AnswerPreference(knowledge_base_id=context.knowledge_base_id, answer_detail="detailed"))
            session.flush()
    with Session(context.engine) as session, pytest.raises(IntegrityError):
        with session.begin():
            session.add(AnswerPreference(knowledge_base_id=uuid4(), answer_detail="detailed"))
            session.flush()
    with Session(context.engine) as session, pytest.raises(IntegrityError):
        with session.begin():
            session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == context.knowledge_base_id))
    assert context.client.get(url(context)).json()["answer_detail"] == "concise"


def test_last_version_is_readable_but_cannot_overflow_on_update(retrieval_context):
    context = retrieval_context
    write(context)
    with Session(context.engine) as session, session.begin():
        session.execute(update(AnswerPreference).where(AnswerPreference.knowledge_base_id == context.knowledge_base_id)
                        .values(version=MAX_PREFERENCE_VERSION - 1))
    assert write(context, "detailed", MAX_PREFERENCE_VERSION - 1).json()["version"] == MAX_PREFERENCE_VERSION
    assert write(context, "concise", MAX_PREFERENCE_VERSION).status_code == 422
    assert context.client.get(url(context)).json()["version"] == MAX_PREFERENCE_VERSION


def test_commit_failure_rolls_back_setting_and_retry_can_succeed(retrieval_context):
    context = retrieval_context
    payload = PreferenceUpdate(expected_version=0, answer_detail="concise")
    with Session(context.engine) as session:
        def fail_commit(current):
            raise RuntimeError("受控偏好提交故障")
        event.listen(session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="受控偏好提交故障"):
            set_preference(session, context.workspace_id, context.knowledge_base_id, payload)
    assert context.client.get(url(context)).json()["version"] == 0
    with Session(context.engine) as session:
        assert set_preference(session, context.workspace_id, context.knowledge_base_id, payload).version == 1


def test_constructed_invalid_internal_update_is_revalidated(retrieval_context):
    context = retrieval_context
    payload = PreferenceUpdate.model_construct(expected_version=True, answer_detail="concise")
    with Session(context.engine) as session, pytest.raises(PreferenceStateError, match="回答偏好更新无效"):
        set_preference(session, context.workspace_id, context.knowledge_base_id, payload)
    assert context.client.get(url(context)).json()["version"] == 0


@pytest.mark.parametrize("changes", [{"version": -1}, {"version": True},
                                    {"version": 0, "answer_detail": "concise"},
                                    {"answer_detail": "unknown"}, {"free_text": "unbounded"}])
def test_preference_snapshot_contract_rejects_invalid_or_extra_state(changes):
    with pytest.raises(ValidationError):
        PreferenceRead(**{"knowledge_base_id": uuid4(), "version": 1, "answer_detail": "concise", **changes})


@pytest.mark.parametrize("changes", [{"version": 0}, {"version": True}, {"version": -1},
                                    {"answer_detail": "private-invalid-content"}])
def test_loading_corrupted_row_raises_fixed_domain_error_without_echoing_contents(changes):
    identifier = uuid4()
    row = SimpleNamespace(**{"knowledge_base_id": identifier, "version": 1,
                             "answer_detail": "concise", **changes})
    class CorruptedSession:
        def execute(self, statement):
            return self
        def one_or_none(self):
            return row
    with pytest.raises(PreferenceStateError) as raised:
        load_preference(CorruptedSession(), identifier)
    assert str(raised.value) == "回答偏好状态无效" and "private-invalid-content" not in str(raised.value)


@pytest.mark.parametrize("rows", [[], [{"answer_detail": "concise"}],
                                  [{"answer_detail": "detailed"}], [{"answer_detail": None}]])
def test_migration_downgrade_locks_before_check_and_preserves_all_nonempty_rows(rows, monkeypatch):
    # 不连接数据库：只验证降级函数的调用顺序，真实锁阻塞仍由数据库集成验收。
    migration = load_migration(Path(__file__).parents[1] / "alembic" / "versions" /
                               "20261012_0008_answer_preferences.py")
    calls = []
    class FakeBind:
        def execute(self, statement):
            calls.append(("execute", " ".join(str(statement).split())))
        def scalar(self, statement):
            calls.append(("scalar", " ".join(str(statement).split())))
            return bool(rows)
    bind = FakeBind()
    class FakeOperations:
        def get_bind(self):
            return bind
        def drop_table(self, name):
            calls.append(("drop_table", name))
    monkeypatch.setattr(migration, "op", FakeOperations())
    if rows:
        with pytest.raises(RuntimeError, match="不能无损降级"):
            migration.downgrade()
    else:
        migration.downgrade()
    # 首先取得排他表锁，再检查任意记录；不能只筛非空偏好而遗漏清除墓碑。
    expected = [("execute", "LOCK TABLE answer_preferences IN ACCESS EXCLUSIVE MODE"),
                ("scalar", "SELECT EXISTS (SELECT 1 FROM answer_preferences)")]
    if not rows:
        expected.append(("drop_table", "answer_preferences"))
    assert calls == expected
