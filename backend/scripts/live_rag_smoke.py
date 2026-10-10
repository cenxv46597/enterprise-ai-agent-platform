"""显式运行真实 RAG 小样本验证；不读取或输出供应商原始报错。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import REPOSITORY_ROOT, Settings
from app.main import create_app


CORPUS_PATH = REPOSITORY_ROOT / "examples/knowledge/demo-product.md"
CASES_PATH = REPOSITORY_ROOT / "examples/evaluation/rag-smoke-cases.json"
REPORT_ROOT = REPOSITORY_ROOT / "docs/evaluation"


class SmokeFailure(RuntimeError):
    """只保存阶段和 HTTP 状态；不携带原始响应或配置。"""

    def __init__(self, stage: str, status_code: int | None = None) -> None:
        self.stage = stage
        self.status_code = status_code
        super().__init__(stage)


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=True), flush=True)


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_local_configuration(settings: Settings) -> None:
    address = make_url(settings.database_url or "")
    if (
        settings.app_env != "local"
        or address.get_backend_name() != "postgresql"
        or address.host not in {"127.0.0.1", "localhost", "::1"}
        or address.database != "eaap"
        # psycopg 会用 query 中的 host/dbname 覆盖 URL 目标，演示脚本拒绝这类参数。
        or address.query
        or not settings.upload_root.resolve().is_relative_to(REPOSITORY_ROOT.resolve())
        or settings.embedding_model != "text-embedding-v4"
        or settings.embedding_dimensions != 1024
        or settings.generation_model != "deepseek-flash"
        or (settings.generation_base_url or "").strip().rstrip("/")
        not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
        or not settings.embedding_api_key
        or not settings.embedding_api_key.get_secret_value().strip()
        or not settings.generation_api_key
        or not settings.generation_api_key.get_secret_value().strip()
    ):
        raise SmokeFailure("local_configuration")
    engine = create_engine(
        settings.database_url,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"},
    )
    try:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            has_vector = connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')"))
            if revision != "20261010_0004" or not has_vector:
                raise SmokeFailure("database_migration")
    finally:
        engine.dispose()


def request(client: TestClient, method: str, url: str, stage: str, **kwargs) -> tuple[object, float]:
    start = perf_counter()
    response = client.request(method, url, **kwargs)
    elapsed = round(perf_counter() - start, 3)
    if response.status_code not in {200, 201}:
        raise SmokeFailure(stage, response.status_code)
    return response.json(), elapsed


def resource_path(report: dict) -> str:
    workspace_id = UUID(report["workspace_id"])
    knowledge_base_id = UUID(report["knowledge_base_id"])
    return f"/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}"


def prepare_demo(client: TestClient, report: dict, report_path: Path, corpus: bytes, resume: bool) -> str:
    workspace_name = "RAG真实联调演示-" + report["run_id"]
    if resume:
        # 恢复只接受同一语料、同一演示资源，避免误操作其他知识库。
        workspace, _ = request(
            client, "GET", f"/api/v1/workspaces/{UUID(report['workspace_id'])}", "resume_workspace"
        )
        if workspace["name"] != workspace_name:
            raise SmokeFailure("resume_scope")
    else:
        workspace, _ = request(client, "POST", "/api/v1/workspaces", "create_workspace", json={"name": workspace_name})
        report["workspace_id"] = workspace["id"]
        save_report(report_path, report)
    if "knowledge_base_id" not in report:
        base, _ = request(
            client, "POST", f"/api/v1/workspaces/{workspace['id']}/knowledge-bases", "create_knowledge_base",
            json={"name": "虚构产品技术说明"},
        )
        report["knowledge_base_id"] = base["id"]
        save_report(report_path, report)
    else:
        base, _ = request(client, "GET", resource_path(report), "resume_knowledge_base")
        if base["name"] != "虚构产品技术说明":
            raise SmokeFailure("resume_scope")
    document_url = resource_path(report) + "/documents"
    if "document_id" not in report:
        document, elapsed = request(
            client, "POST", document_url, "upload_document",
            files={"file": ("demo-product.md", corpus, "text/markdown")},
        )
        report["document_id"] = document["id"]
        report["upload_seconds"] = elapsed
        save_report(report_path, report)
    else:
        document, _ = request(client, "GET", document_url + "/" + str(UUID(report["document_id"])), "resume_document")
    if document["sha256"] != report["corpus_sha256"] or document["original_filename"] != "demo-product.md":
        raise SmokeFailure("document_identity")
    if document["status"] not in {"parsed", "ready"}:
        raise SmokeFailure("document_parse")
    ensure_demo_only(report)
    document_url += "/" + str(UUID(report["document_id"]))
    chunks, elapsed = request(client, "POST", document_url + "/chunks", "persist_chunks", json={"chunk_size": 500, "overlap": 50})
    report["chunk_count"] = len(chunks)
    report["chunk_seconds"] = elapsed
    save_report(report_path, report)
    embedding, elapsed = request(client, "POST", document_url + "/embeddings", "embed_document")
    report.setdefault("initial_embedding", report.get("embedding", embedding))
    report.setdefault("initial_embedding_seconds", report.get("embedding_seconds", elapsed))
    report["embedding"] = embedding
    report["embedding_seconds"] = elapsed
    save_report(report_path, report)
    if embedding["status"] != "ready":
        raise SmokeFailure("embedding_status")
    return resource_path(report)


def ensure_demo_only(report: dict) -> None:
    """重复运行前确认该知识库仍只包含本次演示文档。"""
    engine = create_engine(Settings().database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            identifiers = set(connection.scalars(
                text("SELECT id FROM documents WHERE knowledge_base_id = :knowledge_base_id"),
                {"knowledge_base_id": UUID(report["knowledge_base_id"])},
            ))
        if identifiers != {UUID(report["document_id"])}:
            raise SmokeFailure("demo_documents_changed")
    finally:
        engine.dispose()


def public_span_matches(item: dict, corpus_text: str, document_id: str, content_field: str) -> bool:
    start, end = item.get("start_char"), item.get("end_char")
    return (
        item.get("document_id") == document_id
        and type(start) is int and type(end) is int
        and 0 <= start < end <= len(corpus_text)
        and corpus_text[start:end] == item.get(content_field)
    )


def evaluate_case(client: TestClient, base_url: str, case: dict, corpus_text: str, document_id: str) -> dict:
    result = {
        "id": case["id"], "question": case["question"],
        "expected_abstained": case["expected_abstained"],
    }
    search, elapsed = request(client, "POST", base_url + "/search", "search_" + case["id"], json={"query": case["question"], "top_k": 5})
    if not all(public_span_matches(hit, corpus_text, document_id, "content") for hit in search["results"]):
        raise SmokeFailure("search_sources_changed")
    result["search_seconds"] = elapsed
    result["retrieved_chunk_indices"] = [hit["chunk_index"] for hit in search["results"]]
    result["retrieved_distances"] = [hit["distance"] for hit in search["results"]]
    result["expected_text_checks"] = [
        {"text": expected, "found_in_returned_chunk": any(expected in hit["content"] for hit in search["results"])}
        for expected in case["expected_source_texts"]
    ]
    answer, elapsed = request(client, "POST", base_url + "/answers", "answer_" + case["id"], json={"question": case["question"], "top_k": 5})
    if not all(public_span_matches(citation, corpus_text, document_id, "quote") for citation in answer["citations"]):
        raise SmokeFailure("answer_sources_changed")
    result["answer_seconds"] = elapsed
    result["response"] = answer
    result["abstention_matches_expected"] = answer["abstained"] == case["expected_abstained"]
    result["citation_spans_valid"] = True
    result["answer_term_hints"] = [
        {"term": term, "present": term in answer["answer"]} for term in case["expected_answer_terms"]
    ]
    result["semantic_review"] = "待人工核对；词语命中和引文存在不代表答案忠实"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="真实接口会产生费用；只验证固定公开演示语料并保留本机演示资源。")
    parser.add_argument("--run", action="store_true", help="显式允许真实模型调用与本机演示数据写入")
    parser.add_argument("--resume", action="store_true", help="复用报告中的演示资源，避免重复上传和向量生成")
    parser.add_argument("--case-id", action="append", help="只运行指定的固定问题，可重复提供")
    parser.add_argument("--report", default=str(REPORT_ROOT / "live-rag-smoke-2026-10-10.json"))
    args = parser.parse_args()
    if not args.run:
        parser.error("需要 --run；此脚本调用真实 API 并写入本机演示资源")
    report_path = Path(args.report).resolve()
    if report_path.suffix != ".json" or not report_path.is_relative_to(REPORT_ROOT.resolve()):
        parser.error("报告必须保存在仓库 docs/evaluation 目录内，扩展名为 .json")
    for name in ("httpx", "httpcore", "httpx2", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)
    report: dict = {}
    try:
        check_local_configuration(Settings())
        corpus = CORPUS_PATH.read_bytes()
        corpus_text = CORPUS_PATH.read_text(encoding="utf-8-sig")
        dataset = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        selected = [case for case in dataset["cases"] if not args.case_id or case["id"] in args.case_id]
        if not selected or len(selected) > 10 or (args.case_id and set(args.case_id) != {case["id"] for case in selected}):
            raise SmokeFailure("case_selection")
        corpus_hash = hashlib.sha256(corpus).hexdigest()
        if args.resume:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report["corpus_sha256"] != corpus_hash or report["dataset_version"] != dataset["dataset_version"]:
                raise SmokeFailure("resume_dataset")
        else:
            if report_path.exists():
                raise SmokeFailure("report_exists_use_resume")
            report = {
                "run_id": uuid4().hex[:12], "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "dataset_version": dataset["dataset_version"], "corpus_sha256": corpus_hash,
                "embedding_request_model": "text-embedding-v4", "embedding_dimensions": 1024,
                "generation_request_model": "deepseek-flash", "execution": "TestClient 调用真实路由、本机数据库与供应商 API",
                "scope": "固定小语料的链路验证，不是检索质量基准；未统计 token 或费用，未验证部署网关",
                "attempts": [],
            }
        with TestClient(create_app()) as client:
            base_url = prepare_demo(client, report, report_path, corpus, args.resume)
            emit({"stage": "document_ready", "chunk_count": report["chunk_count"], "reused_embeddings": report["embedding"]["reused"]})
            for case in selected:
                ensure_demo_only(report)
                result = evaluate_case(client, base_url, case, corpus_text, report["document_id"])
                report["attempts"].append(result)
                save_report(report_path, report)
                emit({"stage": "case_complete", "id": result["id"], "abstained": result["response"]["abstained"], "citations": len(result["response"]["citations"]), "answer_seconds": result["answer_seconds"]})
        report.pop("failure", None)
        save_report(report_path, report)
        emit({"stage": "complete", "selected_case_count": len(selected)})
        return 0
    except Exception as exc:
        failure = {"error_type": type(exc).__name__}
        if isinstance(exc, SmokeFailure):
            failure.update(stage=exc.stage, status_code=exc.status_code)
        if report:
            report["failure"] = failure
            try:
                save_report(report_path, report)
            except OSError:
                failure["report_write_failed"] = True
        emit({"stage": "failed", **failure})
        return 2


if __name__ == "__main__":
    sys.exit(main())
