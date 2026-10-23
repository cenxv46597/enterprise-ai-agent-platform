"""文档查找/翻页的固定真实 SQL 节点；复用独立 eaap_test 临时身份和 schema。"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import Document
from test_document_chunk_read_db import chunk_read_db_context  # noqa: F401


def documents_url(context, *, workspace_id=None, knowledge_base_id=None):
    return (f"/api/v1/workspaces/{workspace_id or context.workspace_id}/knowledge-bases/"
            f"{knowledge_base_id or context.knowledge_base_id}/documents")


def insert_document(context, filename, *, identifier=None, knowledge_base_id=None, state="parsed"):
    identifier = identifier or uuid4()
    with Session(context.engine) as session, session.begin():
        session.add(Document(
            id=identifier, knowledge_base_id=knowledge_base_id or context.knowledge_base_id,
            original_filename=filename, media_type="text/markdown", byte_size=10,
            storage_key=f"fixture-not-a-file/{identifier}", sha256="a" * 64, status=state,
            created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ))
    return identifier


def test_db_document_filename_filter_precedes_pagination_and_intersects_status(chunk_read_db_context):
    context = chunk_read_db_context
    ids = [UUID(int=index + 100) for index in range(25)]
    for index, identifier in enumerate(ids):
        insert_document(context, f"{'历史计划' if index >= 20 else '其他资料'}-{index}.md",
                        identifier=identifier, state="failed" if index % 2 == 0 else "parsed")
    url = documents_url(context)
    first = context.client.get(url)
    assert first.status_code == 200
    assert [row["id"] for row in first.json()] == [str(value) for value in ids[:20]]
    matching = context.client.get(url, params={"filename_query": "历史计划"})
    assert [row["id"] for row in matching.json()] == [str(value) for value in ids[20:]]
    for offset, expected in ((0, ids[20:22]), (2, ids[22:24]), (4, ids[24:]), (6, [])):
        page = context.client.get(url, params={"filename_query": "历史计划", "limit": 2, "offset": offset})
        assert page.status_code == 200 and [row["id"] for row in page.json()] == [str(value) for value in expected]
    intersection = context.client.get(url, params={"filename_query": "历史计划", "status": "failed", "limit": 1, "offset": 1})
    assert intersection.status_code == 200 and [row["id"] for row in intersection.json()] == [str(ids[22])]
    second = context.client.get(url, params={"offset": 20})
    assert second.status_code == 200 and [row["id"] for row in second.json()[:5]] == [str(value) for value in ids[20:]]


def test_db_document_filename_matching_is_literal_case_insensitive_and_unicode_bounded(chunk_read_db_context):
    context = chunk_read_db_context
    url = documents_url(context)
    for filename, decoy, query in (
        ("预算100%.md", "预算1000.md", "100%"),
        ("risk_plan.md", "riskXplan.md", "risk_plan"),
        ("a/b.md", "ab.md", "a/b"),
        ("REPORT_FINAL.MD", "NOTES.MD", " report_final "),
        ("😀" * 255, "😀" * 254, "😀" * 255),
    ):
        target = insert_document(context, filename)
        insert_document(context, decoy)
        response = context.client.get(url, params={"filename_query": query})
        assert response.status_code == 200 and [row["id"] for row in response.json()] == [str(target)]
    assert context.client.get(url, params={"filename_query": "无此文件"}).json() == []
    for query in ("", "  ", "😀" * 256, "资料\x00.md"):
        assert context.client.get(url, params={"filename_query": query}).status_code == 422


def test_db_document_filename_lookup_preserves_membership_and_parent_scope(chunk_read_db_context):
    context = chunk_read_db_context
    own = insert_document(context, "范围共同线索.md")
    insert_document(context, "范围共同线索.md", knowledge_base_id=context.other_base_id)
    insert_document(context, "范围共同线索.md", knowledge_base_id=context.foreign_base_id)
    url, params = documents_url(context), {"filename_query": "范围共同线索"}
    for user_id in (context.owner_id, context.member_id):
        context.as_user(user_id)
        response = context.client.get(url, params=params)
        assert response.status_code == 200 and [row["id"] for row in response.json()] == [str(own)]
    context.as_user(context.outsider_id)
    assert context.client.get(url, params=params).status_code == 404
    context.as_user(context.owner_id)
    wrong = context.client.get(documents_url(context, workspace_id=context.other_workspace_id), params=params)
    assert wrong.status_code == 404 and wrong.json() == {"detail": "知识库不存在"}
    missing = context.client.get(documents_url(context, knowledge_base_id=uuid4()), params=params)
    assert missing.status_code == 404


def test_db_document_listing_only_selects_metadata_and_leaves_data_unchanged(chunk_read_db_context):
    context = chunk_read_db_context
    insert_document(context, "只读查询材料.md")
    before = context.snapshot()
    # read() asserts every request statement is a SELECT, including membership and parent checks.
    response, statements = context.read(documents_url(context) + "?filename_query=材料&limit=1&offset=0")
    assert response.status_code == 200 and len(response.json()) == 1
    assert "storage_key" not in response.text and "embedding" not in response.text and "content" not in response.text
    assert all("chunks" not in sql and "embedding" not in sql for sql, _compiled in statements)
    assert context.snapshot() == before
