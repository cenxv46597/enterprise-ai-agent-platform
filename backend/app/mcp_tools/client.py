"""官方 SDK 负责消息传输；本地白名单与固定 schema 决定哪些工具可调用。"""

import os
import sys
from typing import Protocol
from uuid import UUID

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, CallToolResult, ListToolsResult

from app.core.config import REPOSITORY_ROOT, Settings
from app.knowledge.retrieval import SearchStrategy
from app.mcp_tools.contracts import (
    MAX_MCP_DISCOVERED_TOOLS, McpSearchError, SearchEnvelope,
    approved_input_schema, approved_output_schema, wire_json,
)


class McpSearchClient(Protocol):
    def search(self, query: str) -> SearchEnvelope: ...


def public_error_code(error: Exception) -> str:
    """TaskGroup 可能包装多层异常；只回传一致、已知的叶子故障类别。"""
    pending: list[BaseException] = [error]
    leaves: list[BaseException] = []
    while pending:
        item = pending.pop()
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions)
        else:
            leaves.append(item)
    if all(isinstance(item, TimeoutError) or isinstance(item, MCPError) and item.code == REQUEST_TIMEOUT
           for item in leaves):
        return "mcp_timeout"
    if all(isinstance(item, McpSearchError) for item in leaves):
        codes = {item.code for item in leaves}
        if len(codes) == 1 and codes <= {"mcp_timeout", "mcp_service_error", "mcp_result_invalid", "mcp_discovery_invalid"}:
            return codes.pop()
    return "mcp_service_error"


def validate_discovery(listing: ListToolsResult) -> None:
    try:
        wire_json(listing.model_dump(mode="json"))
    except (McpSearchError, TypeError, ValueError):
        raise McpSearchError("mcp_discovery_invalid") from None
    if (listing.result_type != "complete" or listing.next_cursor is not None
            or len(listing.tools) > MAX_MCP_DISCOVERED_TOOLS
            or len({tool.name for tool in listing.tools}) != len(listing.tools)):
        raise McpSearchError("mcp_discovery_invalid")
    # 其他名称不会进入 ToolRegistry，也不会被转成模型提示中的工具描述。
    matches = [tool for tool in listing.tools if tool.name == "knowledge_search"]
    if (len(matches) != 1 or matches[0].input_schema != approved_input_schema()
            or matches[0].output_schema != approved_output_schema()):
        raise McpSearchError("mcp_discovery_invalid")


async def exchange(client: Client, query: str) -> SearchEnvelope:
    listing = await client.list_tools(cache_mode="refresh")
    validate_discovery(listing)
    # 使用单次低层调用，避免高层 Client 的 schema 变化或补充输入自动重发。
    result = await client.session.call_tool("knowledge_search", {"query": query})
    if (not isinstance(result, CallToolResult) or result.result_type != "complete"
            or result.is_error or result.structured_content is None):
        raise McpSearchError("mcp_result_invalid")
    data = wire_json(result.structured_content)
    # 只接受同一结构化内容的 JSON 文本，不消费图片、链接或附加自由指令。
    if (len(result.content) != 1 or result.content[0].type != "text"
            or result.content[0].text != data):
        raise McpSearchError("mcp_result_invalid")
    try:
        return SearchEnvelope.model_validate_json(data)
    except (TypeError, ValueError):
        raise McpSearchError("mcp_result_invalid") from None


class StdioSearchClient:
    """每次搜索一个短命进程；不缓存跨 event loop 的 Session，不支持任意命令。"""
    def __init__(self, workspace_id: UUID, knowledge_base_id: UUID, strategy: SearchStrategy = "vector") -> None:
        self.workspace_id, self.knowledge_base_id, self.strategy = workspace_id, knowledge_base_id, strategy

    async def _search(self, query: str) -> SearchEnvelope:
        settings = Settings()
        child_env = {
            "PYTHONUTF8": "1", "EAAP_DATABASE_URL": settings.database_url or "",
            "EAAP_EMBEDDING_BASE_URL": settings.embedding_base_url or "",
            "EAAP_EMBEDDING_API_KEY": settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else "",
            "EAAP_EMBEDDING_MODEL": settings.embedding_model,
            "EAAP_EMBEDDING_DIMENSIONS": str(settings.embedding_dimensions),
            "EAAP_EMBEDDING_TIMEOUT_SECONDS": str(settings.embedding_timeout_seconds),
            "EAAP_EMBEDDING_TOTAL_TIMEOUT_SECONDS": str(settings.embedding_total_timeout_seconds),
        }
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp_tools.server", "--workspace-id", str(self.workspace_id),
                                         "--knowledge-base-id", str(self.knowledge_base_id), "--strategy", self.strategy],
            env=child_env, cwd=REPOSITORY_ROOT,
        )
        # 不把子进程 stderr（可能含第三方异常）转发到公开响应或聊天。
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            with anyio.fail_after(settings.mcp_tool_timeout_seconds):
                async with Client(stdio_client(parameters, errlog=errlog), cache=None,
                                  read_timeout_seconds=settings.mcp_tool_timeout_seconds) as client:
                    return await exchange(client, query)

    def search(self, query: str) -> SearchEnvelope:
        try:
            return anyio.run(self._search, query)
        except Exception as error:
            raise McpSearchError(public_error_code(error)) from None
