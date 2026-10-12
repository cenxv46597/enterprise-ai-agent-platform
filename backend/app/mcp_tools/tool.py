"""Host 在短事务回验完整批次原文，再接入已有 Agent 证据池。"""

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid import UUID

from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.agents.tools import KnowledgeSearchTool, SearchArguments
from app.db.models import Chunk, Document, KnowledgeBase
from app.knowledge.retrieval import SearchHit, SearchStrategy
from app.mcp_tools.client import McpSearchClient
from app.mcp_tools.contracts import McpSearchError, SearchEnvelope, wire_json


def verify_evidence(session: Session, envelope: SearchEnvelope, workspace_id: UUID,
                    knowledge_base_id: UUID, strategy: SearchStrategy) -> tuple[SearchHit, ...]:
    if (envelope.workspace_id != workspace_id or envelope.knowledge_base_id != knowledge_base_id
            or envelope.strategy != strategy):
        raise McpSearchError("mcp_evidence_invalid")
    with session.begin():
        scope = session.scalar(select(KnowledgeBase.id).where(
            KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == workspace_id,
        ).with_for_update(read=True))
        if scope is None:
            raise McpSearchError("mcp_evidence_invalid")
        rows = session.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(
            Chunk.id.in_([hit.chunk_id for hit in envelope.hits]), Document.knowledge_base_id == knowledge_base_id,
            Document.status == "ready", Chunk.embedding.is_not(None),
        ).execution_options(populate_existing=True).with_for_update(read=True)).all() if envelope.hits else []
        local = {chunk.id: (chunk, document) for chunk, document in rows}
        result = []
        for hit in envelope.hits:
            row = local.get(hit.chunk_id)
            if row is None:
                raise McpSearchError("mcp_evidence_invalid")
            chunk, document = row
            expected = (document.id, document.original_filename, chunk.chunk_index, chunk.content,
                        chunk.start_char, chunk.end_char)
            received = (hit.document_id, hit.document_name, hit.chunk_index, hit.content, hit.start_char, hit.end_char)
            if received != expected:
                raise McpSearchError("mcp_evidence_invalid")
            # 使用已回验的本地字段；距离/排名只作为远端检索元数据，不是正确率。
            result.append(SearchHit(**hit.model_dump()))
        return tuple(result)


class McpKnowledgeSearchTool:
    name = KnowledgeSearchTool.name
    description = KnowledgeSearchTool.description
    argument_schema = SearchArguments

    def __init__(self, session: Session, client: McpSearchClient, workspace_id: UUID, knowledge_base_id: UUID,
                 *, strategy: SearchStrategy = "vector") -> None:
        self.session, self.client = session, client
        self.workspace_id, self.knowledge_base_id, self.strategy = workspace_id, knowledge_base_id, strategy

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        if not isinstance(arguments, SearchArguments):
            raise ToolExecutionError("invalid_arguments")
        try:
            if self.session.in_transaction():
                raise McpSearchError("mcp_transaction_open")
            raw = self.client.search(arguments.query)
            try:
                envelope = SearchEnvelope.model_validate_json(wire_json(raw.model_dump(mode="json")))
            except (AttributeError, TypeError, ValueError):
                raise McpSearchError("mcp_result_invalid") from None
            hits = verify_evidence(self.session, envelope, self.workspace_id, self.knowledge_base_id, self.strategy)
            return ToolOutput(hits)
        except McpSearchError as error:
            raise ToolExecutionError(error.code) from None
        except Exception:
            raise ToolExecutionError("mcp_service_error") from None
