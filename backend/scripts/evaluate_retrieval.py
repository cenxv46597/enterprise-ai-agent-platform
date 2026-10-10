"""在独立公开语料上检查预期证据覆盖；真实调用须显式 --run。"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import REPOSITORY_ROOT, Settings
from app.evaluation.retrieval import prepare_cases, evaluate_case
from app.knowledge.chunking import CHUNKING_VERSION, split_text
from app.providers.compatible_embeddings import OpenAICompatibleEmbeddingProvider
from live_rag_smoke import (
    SmokeFailure, check_local_configuration, create_app, emit, public_span_matches,
    request, resource_path, save_report,
)


DATASET_PATH = REPOSITORY_ROOT / "examples/evaluation/retrieval-eval-cases.json"
KNOWLEDGE_ROOT = REPOSITORY_ROOT / "examples/knowledge"
REPORT_ROOT = REPOSITORY_ROOT / "docs/evaluation"
CHUNKING = {"chunk_size": 500, "overlap": 50, "version": CHUNKING_VERSION}
COMPARISON = "同一次实际 top_k=10 响应的前 5 个与前 10 个；只有一次检索耗时"
BASE_NAME = "虚构产品检索评测"


@dataclass(frozen=True)
class Fixture:
    name: str
    data: bytes
    content: str
    sha256: str
    chunk_count: int


def load_dataset(path: Path) -> tuple[dict, tuple[Fixture, ...], dict]:
    raw = path.read_bytes()
    if len(raw) > 100_000:
        raise SmokeFailure("dataset_size")
    dataset = json.loads(raw)
    if not isinstance(dataset, dict) or not isinstance(dataset.get("dataset_version"), str):
        raise SmokeFailure("dataset_schema")
    rows = dataset.get("documents")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 10:
        raise SmokeFailure("dataset_documents")
    fixtures = []
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "document_name"}:
            raise SmokeFailure("dataset_document_fields")
        name, relative = row["document_name"], row["path"]
        if not isinstance(name, str) or not isinstance(relative, str) or Path(relative).is_absolute():
            raise SmokeFailure("dataset_document_path")
        source = (REPOSITORY_ROOT / relative).resolve()
        if (
            not source.is_relative_to(KNOWLEDGE_ROOT.resolve())
            or source.name != name or source.suffix.lower() not in {".txt", ".md"}
            or name in names
        ):
            raise SmokeFailure("dataset_document_path")
        names.add(name)
        data = source.read_bytes()
        if not 0 < len(data) <= 100_000:
            raise SmokeFailure("corpus_size")
        # 与 LocalDocumentStore.read_text_utf8 的通用换行解析一致。
        content = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        chunks = split_text(UUID(int=0), content, chunk_size=500, overlap=50)
        fixtures.append(Fixture(name, data, content, hashlib.sha256(data).hexdigest(), len(chunks)))
    if not 20 <= sum(item.chunk_count for item in fixtures) <= 50:
        raise SmokeFailure("candidate_count")
    sources = {item.name: {"name": item.name, "text": item.content} for item in fixtures}
    cases = prepare_cases(dataset.get("cases"), sources)
    if len(cases) > 10 or not any(case.answerable for case in cases):
        raise SmokeFailure("dataset_cases")
    identity = {
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "corpus_sha256": {item.name: item.sha256 for item in fixtures},
        "chunking": CHUNKING,
        "comparison": COMPARISON,
    }
    return dataset, tuple(fixtures), identity


def embedding_profile_digest(settings: Settings) -> str:
    provider = OpenAICompatibleEmbeddingProvider(
        base_url=settings.embedding_base_url, api_key=settings.embedding_api_key,
        model=settings.embedding_model, dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        timeout_seconds=settings.embedding_timeout_seconds,
        total_timeout_seconds=settings.embedding_total_timeout_seconds,
    )
    # 只记录接口身份摘要，报告不保存地域或业务空间地址。
    value = [provider.profile.provider, provider.profile.model, provider.profile.dimensions]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()


def verify_document_set(settings: Settings, report: dict) -> None:
    expected = {UUID(row["document_id"]) for row in report["documents"].values()}
    if "knowledge_base_id" not in report:
        if expected:
            raise SmokeFailure("resume_scope")
        return
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"})
    try:
        with engine.connect() as connection:
            actual = set(connection.scalars(
                text("SELECT id FROM documents WHERE knowledge_base_id = :identifier"),
                {"identifier": UUID(report["knowledge_base_id"])},
            ))
        if actual != expected:
            raise SmokeFailure("evaluation_documents_changed")
    finally:
        engine.dispose()


def validate_document(document: dict, fixture: Fixture, report: dict) -> None:
    if (
        document["original_filename"] != fixture.name or document["sha256"] != fixture.sha256
        or document["knowledge_base_id"] != report["knowledge_base_id"]
        or document["status"] not in {"parsed", "ready"}
    ):
        raise SmokeFailure("document_identity")


def validate_resume(client: TestClient, settings: Settings, report: dict, fixtures: tuple[Fixture, ...]) -> None:
    if not isinstance(report.get("documents"), dict) or set(report["documents"]) - {f.name for f in fixtures}:
        raise SmokeFailure("resume_documents")
    if "workspace_id" not in report:
        if "knowledge_base_id" in report or report["documents"]:
            raise SmokeFailure("resume_scope")
        return
    workspace, _ = request(client, "GET", f"/api/v1/workspaces/{UUID(report['workspace_id'])}", "resume_workspace")
    if workspace["name"] != "检索评测演示-" + report["run_id"]:
        raise SmokeFailure("resume_scope")
    if "knowledge_base_id" in report:
        base, _ = request(client, "GET", resource_path(report), "resume_knowledge_base")
        if base["name"] != BASE_NAME or base["workspace_id"] != report["workspace_id"]:
            raise SmokeFailure("resume_scope")
    verify_document_set(settings, report)
    for fixture in fixtures:
        if fixture.name in report["documents"]:
            record = report["documents"][fixture.name]
            if record["sha256"] != fixture.sha256:
                raise SmokeFailure("resume_document_hash")
            document, _ = request(client, "GET", resource_path(report) + "/documents/" + str(UUID(record["document_id"])), "resume_document")
            validate_document(document, fixture, report)


def validate_chunks(chunks: list, fixture: Fixture, document_id: str) -> None:
    expected = split_text(UUID(document_id), fixture.content, chunk_size=500, overlap=50)
    if len(chunks) != len(expected):
        raise SmokeFailure("document_chunks_changed")
    for actual, wanted in zip(chunks, expected, strict=True):
        if (
            not public_span_matches(actual, fixture.content, document_id, "content")
            or actual["chunk_index"] != wanted.chunk_index
            or actual["start_char"] != wanted.start_char or actual["end_char"] != wanted.end_char
        ):
            raise SmokeFailure("document_chunks_changed")


def prepare_corpus(client: TestClient, settings: Settings, report: dict, fixtures: tuple[Fixture, ...], path: Path) -> dict:
    if "workspace_id" not in report:
        workspace, _ = request(client, "POST", "/api/v1/workspaces", "create_workspace", json={"name": "检索评测演示-" + report["run_id"]})
        report["workspace_id"] = workspace["id"]
        save_report(path, report)
    if "knowledge_base_id" not in report:
        base, _ = request(client, "POST", f"/api/v1/workspaces/{UUID(report['workspace_id'])}/knowledge-bases", "create_knowledge_base", json={"name": BASE_NAME})
        report["knowledge_base_id"] = base["id"]
        save_report(path, report)
    base_url = resource_path(report)
    for fixture in fixtures:
        verify_document_set(settings, report)
        if fixture.name not in report["documents"]:
            document, seconds = request(client, "POST", base_url + "/documents", "upload_document", files={"file": (fixture.name, fixture.data, "text/markdown")})
            validate_document(document, fixture, report)
            report["documents"][fixture.name] = {"document_id": document["id"], "sha256": fixture.sha256, "upload_seconds": seconds}
            save_report(path, report)
        record = report["documents"][fixture.name]
        document_url = base_url + "/documents/" + str(UUID(record["document_id"]))
        chunks, _ = request(client, "POST", document_url + "/chunks", "persist_chunks", json={"chunk_size": 500, "overlap": 50})
        validate_chunks(chunks, fixture, record["document_id"])
        verify_document_set(settings, report)
        embedded, seconds = request(client, "POST", document_url + "/embeddings", "embed_document")
        if embedded["status"] != "ready" or embedded["chunk_count"] != fixture.chunk_count:
            raise SmokeFailure("embedding_status")
        record.setdefault("initial_embedding", embedded)
        record.setdefault("initial_embedding_seconds", seconds)
        record.update(embedding=embedded, embedding_seconds=seconds, chunk_count=fixture.chunk_count)
        save_report(path, report)
        emit({"stage": "document_ready", "document_name": fixture.name, "chunk_count": fixture.chunk_count, "reused": embedded["reused"]})
    verify_document_set(settings, report)
    report["candidate_count"] = sum(row["chunk_count"] for row in report["documents"].values())
    if report["candidate_count"] != sum(f.chunk_count for f in fixtures):
        raise SmokeFailure("candidate_count_changed")
    return {report["documents"][f.name]["document_id"]: {"name": f.name, "text": f.content} for f in fixtures}


def run_case(client: TestClient, base_url: str, case, public_sources: dict) -> dict:
    search, seconds = request(client, "POST", base_url + "/search", "search_" + case.case_id, json={"query": case.question, "top_k": 10})
    # 先验证全部来源，再把公开响应写入报告。前 5 个是同一次排名的前缀。
    evaluations = [evaluate_case(case, search["results"], public_sources, top_k=k).to_dict() for k in (5, 10)]
    return {
        "case_id": case.case_id, "question": case.question, "answerable": case.answerable,
        "request_top_k": 10, "search_seconds": seconds, "results": search["results"],
        "evaluations": evaluations, "generation_called": False,
        "answer_review": "本轮仅评测检索；没有生成答案，不评价弃答或忠实性",
    }


def summarize(attempts: list[dict], dataset: dict) -> dict:
    latest = {row["case_id"]: row for row in attempts}
    answerable = [row for row in latest.values() if row["answerable"]]
    by_k = {}
    for index, k in enumerate((5, 10)):
        scores = [row["evaluations"][index] for row in answerable]
        expected = sum(row["expected_evidence_count"] for row in scores)
        covered = sum(row["covered_evidence_count"] for row in scores)
        by_k[str(k)] = {
            "completed_answerable_cases": len(scores),
            "all_required_evidence_cases": sum(row["all_evidence_found"] for row in scores),
            "required_evidence_count": expected, "covered_evidence_count": covered,
            "evidence_hit_fraction": covered / expected if expected else None,
        }
    return {
        "dataset_complete": set(latest) == {row["id"] for row in dataset["cases"]},
        "completed_unique_cases": len(latest), "top_k": by_k,
        "negative_cases_excluded_from_evidence_score": sum(not row["answerable"] for row in latest.values()),
        "metric_scope": "仅预先标注的必需证据覆盖，不是完整相关性标注的 Recall@K",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="固定公开语料的检索证据评测；--run 会产生向量 API 费用。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="纯本地核对语料、标注和预计块数，不读模型配置")
    mode.add_argument("--run", action="store_true", help="调用真实向量 API 并写入本机独立演示知识库")
    parser.add_argument("--resume", action="store_true", help="复用当前机器报告中的评测资源")
    parser.add_argument("--case-id", action="append", help="只执行指定固定题目，可重复提供")
    parser.add_argument("--report", default=str(REPORT_ROOT / "retrieval-eval-2026-10-10.json"))
    args = parser.parse_args()
    path = Path(args.report).resolve()
    if path.suffix != ".json" or not path.is_relative_to(REPORT_ROOT.resolve()) or (args.plan and args.resume):
        parser.error("报告须位于 docs/evaluation，使用 .json；--resume 只适用于 --run")
    for name in ("httpx", "httpcore", "httpx2", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)
    report = {}
    can_save = False
    try:
        dataset, fixtures, identity = load_dataset(DATASET_PATH)
        selected = [row for row in dataset["cases"] if not args.case_id or row["id"] in args.case_id]
        if not selected or (args.case_id and set(args.case_id) != {row["id"] for row in selected}):
            raise SmokeFailure("case_selection")
        if args.plan:
            emit({"stage": "plan", "documents": [{"name": f.name, "chunk_count": f.chunk_count} for f in fixtures], "candidate_count": sum(f.chunk_count for f in fixtures), "case_count": len(selected), "comparison": COMPARISON, "generation_called": False})
            return 0
        settings = Settings()
        check_local_configuration(settings, require_generation=False)
        identity["embedding_profile_sha256"] = embedding_profile_digest(settings)
        if args.resume:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("identity") != identity:
                raise SmokeFailure("resume_identity")
        else:
            if path.exists():
                raise SmokeFailure("report_exists_use_resume")
            report = {
                "run_id": uuid4().hex[:12], "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "identity": identity, "documents": {}, "attempts": [],
                "embedding_request_model": settings.embedding_model, "embedding_dimensions": settings.embedding_dimensions,
                "execution": "TestClient 调用真实路由、本机数据库和向量供应商 API",
                "scope": "独立虚构语料，固定证据标注；未调用回答模型，未统计 token/费用或验证部署网关",
            }
            can_save = True
        with TestClient(create_app()) as client:
            if args.resume:
                validate_resume(client, settings, report, fixtures)
                can_save = True
            public_sources = prepare_corpus(client, settings, report, fixtures, path)
            cases = prepare_cases(selected, public_sources)
            for case in cases:
                verify_document_set(settings, report)
                result = run_case(client, resource_path(report), case, public_sources)
                report["attempts"].append(result)
                report["summary"] = summarize(report["attempts"], dataset)
                save_report(path, report)
                emit({"stage": "case_complete", "case_id": case.case_id, "evidence_hits": [{"top_k": s["top_k"], "found": s["covered_evidence_count"], "expected": s["expected_evidence_count"], "all_found": s["all_evidence_found"]} for s in result["evaluations"]]})
        report.pop("failure", None)
        save_report(path, report)
        emit({"stage": "complete", "summary": report["summary"]})
        return 0
    except Exception as exc:
        failure = {"error_type": type(exc).__name__}
        if isinstance(exc, SmokeFailure):
            failure.update(stage=exc.stage, status_code=exc.status_code)
        if can_save:
            report["failure"] = failure
            try:
                save_report(path, report)
            except OSError:
                failure["report_write_failed"] = True
        emit({"stage": "failed", **failure})
        return 2


if __name__ == "__main__":
    sys.exit(main())
