"""自建固定范围知识搜索 MCP Server；stdout 只用于协议，由 SDK 管理。"""

import argparse
from collections.abc import Callable
from uuid import UUID

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, PaginatedRequestParams, TextContent, Tool, ToolAnnotations
from sqlalchemy.orm import Session

from app.agents.tools import KnowledgeSearchTool, SearchArguments
from app.core.config import Settings
from app.db.session import get_engine
from app.knowledge.retrieval import SearchStrategy
from app.mcp_tools.contracts import SearchEnvelope, SearchHitPayload, approved_input_schema, approved_output_schema, wire_json
from app.providers.compatible_embeddings import OpenAICompatibleEmbeddingProvider


def create_server(search: Callable[[SearchArguments], SearchEnvelope]) -> Server:
    """显式 schema 复用既有契约；不根据远端自由文本生成执行函数。"""
    async def list_tools(context: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=[Tool(
            name="knowledge_search", description=KnowledgeSearchTool.description,
            inputSchema=approved_input_schema(), outputSchema=approved_output_schema(),
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        )])

    async def call_tool(context: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        try:
            # 即使客户端绕过发现，服务端也不会分派未开放工具。
            if params.name != "knowledge_search":
                raise ValueError("未开放工具")
            arguments = SearchArguments.model_validate(params.arguments, strict=True)
            result = await anyio.to_thread.run_sync(search, arguments)
            envelope = SearchEnvelope.model_validate_json(wire_json(result.model_dump(mode="json")))
            data = envelope.model_dump(mode="json")
            return CallToolResult(content=[TextContent(type="text", text=wire_json(data))], structuredContent=data)
        except Exception:
            # 业务错误和配置错误均不带原始异常、路径或连接信息离开进程。
            return CallToolResult(content=[TextContent(type="text", text="知识搜索工具执行失败")], isError=True)

    return Server("enterprise-knowledge-search", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool)


def configured_search(workspace_id: UUID, knowledge_base_id: UUID,
                      strategy: SearchStrategy) -> Callable[[SearchArguments], SearchEnvelope]:
    def search(arguments: SearchArguments) -> SearchEnvelope:
        settings = Settings()
        if not settings.embedding_base_url or not settings.embedding_api_key:
            raise ValueError("向量服务尚未配置")
        provider = OpenAICompatibleEmbeddingProvider(
            settings.embedding_base_url, settings.embedding_api_key, settings.embedding_model,
            settings.embedding_dimensions, settings.embedding_batch_size, settings.embedding_timeout_seconds,
            settings.embedding_total_timeout_seconds,
        )
        # Session 在此工作线程创建和关闭，不跨线程共享 Host 的会话。
        with Session(get_engine()) as session:
            output = KnowledgeSearchTool(session, provider, workspace_id, knowledge_base_id,
                                         strategy=strategy).execute(arguments)
        return SearchEnvelope(workspace_id=workspace_id, knowledge_base_id=knowledge_base_id,
                              strategy=strategy, hits=[SearchHitPayload.from_hit(hit) for hit in output.hits])
    return search


async def serve(server: Server) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description="固定范围的只读知识搜索 MCP Server")
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--strategy", choices=("vector", "hybrid"), default="vector")
    args = parser.parse_args()
    anyio.run(serve, create_server(configured_search(args.workspace_id, args.knowledge_base_id, args.strategy)))


if __name__ == "__main__":
    main()
