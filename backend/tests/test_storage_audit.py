"""通过故障注入验证崩溃窗口和只读对账的边界。"""

from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.knowledge import audit
from app.knowledge.audit import compare_storage
from app.knowledge.ingestion import ingest_document
from app.knowledge.storage import LocalDocumentStore


def test_process_exit_after_save_leaves_a_detectable_orphan(tmp_path: Path) -> None:
    class ExitingSession:
        def add(self, document) -> None:
            # SystemExit 绕过 Exception 补偿，模拟首次提交前进程退出的窗口。
            raise SystemExit("模拟退出")

    store = LocalDocumentStore(tmp_path)
    with pytest.raises(SystemExit, match="模拟退出"):
        ingest_document(
            session=ExitingSession(),
            store=store,
            source=BytesIO(b"guide"),
            filename="guide.md",
            knowledge_base_id=uuid4(),
            max_bytes=1024,
        )
    files = store.published_files()
    assert len(files) == 1
    key, modified = next(iter(files.items()))
    before_grace = compare_storage(store, [], now=modified + 3599)
    assert before_grace.recent_untracked == (key,)
    assert before_grace.orphan_candidates == ()
    at_grace = compare_storage(store, [], now=modified + 3600)
    assert at_grace.orphan_candidates == (key,)
    assert at_grace.recent_untracked == ()
    assert (tmp_path / key).read_bytes() == b"guide"


def test_audit_preserves_files_and_reports_both_directions(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)
    referenced = store.save(BytesIO(b"existing"), uuid4(), 1024).storage_key
    orphan = store.save(BytesIO(b"orphan"), uuid4(), 1024).storage_key
    missing = f"documents/{uuid4().hex}"
    os.utime(tmp_path / referenced, (1, 1))
    os.utime(tmp_path / orphan, (1, 1))
    temporary = tmp_path / "documents" / ".unfinished.tmp"
    temporary.write_bytes(b"partial")
    before = {path.name: path.read_bytes() for path in (tmp_path / "documents").iterdir()}

    first = compare_storage(store, [referenced, missing], now=4000)
    second = compare_storage(store, [referenced, missing], now=4000)
    assert first == second
    assert first.orphan_candidates == (orphan,)
    assert first.missing_files == (missing,)
    assert first.recent_untracked == ()
    assert before == {path.name: path.read_bytes() for path in (tmp_path / "documents").iterdir()}


def test_new_reference_is_used_on_the_next_audit(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)
    key = store.save(BytesIO(b"guide"), uuid4(), 1024).storage_key
    assert compare_storage(store, [], grace_seconds=0).orphan_candidates == (key,)
    assert compare_storage(store, [key], grace_seconds=0).orphan_candidates == ()


def test_negative_grace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="宽限期"):
        compare_storage(LocalDocumentStore(tmp_path), [], grace_seconds=-1)


def test_cli_refuses_a_missing_root_without_creating_it(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path / "missing"
    monkeypatch.setenv("EAAP_UPLOAD_ROOT", str(root))
    monkeypatch.setattr(audit, "get_session_factory", lambda: pytest.fail("不应访问数据库"))
    assert audit.main([]) == 2
    assert not root.exists()
    assert "对账失败" in capsys.readouterr().err


def test_database_read_failure_does_not_report_every_file_as_orphan(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    store = LocalDocumentStore(tmp_path)
    key = store.save(BytesIO(b"guide"), uuid4(), 1024).storage_key

    class FailedSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalars(self, statement):
            raise OperationalError("SELECT storage_key", {}, RuntimeError("内部连接详情"))

    monkeypatch.setenv("EAAP_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(audit, "get_session_factory", lambda: FailedSession)
    assert audit.main([]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "无法读取数据库" in output.err
    assert "内部连接详情" not in output.err
    assert (tmp_path / key).read_bytes() == b"guide"


@pytest.mark.parametrize("with_orphan", [False, True])
def test_cli_outputs_json_and_exit_status(tmp_path: Path, monkeypatch, capsys, with_orphan: bool) -> None:
    store = LocalDocumentStore(tmp_path)
    if with_orphan:
        store.save(BytesIO(b"guide"), uuid4(), 1024)

    class EmptyResult:
        def all(self):
            return []

    class ReadSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalars(self, statement):
            return EmptyResult()

    monkeypatch.setenv("EAAP_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(audit, "get_session_factory", lambda: ReadSession)
    assert audit.main(["--grace-seconds", "0"]) == int(with_orphan)
    report = json.loads(capsys.readouterr().out)
    assert bool(report["orphan_candidates"]) is with_orphan
    assert report["missing_files"] == []
