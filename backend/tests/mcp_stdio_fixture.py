"""仅测试使用的真实 stdio 子进程；不读取配置、数据库或调用模型。"""

import argparse
from uuid import UUID

import anyio

from app.mcp_tools.contracts import SearchEnvelope, SearchHitPayload
from app.mcp_tools.server import create_server, serve


WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
KNOWLEDGE_BASE_ID = UUID("22222222-2222-4222-8222-222222222222")
PUBLIC_CONTENT = "公开计费标准：月费 120 元，每次上门服务费 30 元。"


def public_search(arguments):
    return SearchEnvelope(
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        strategy="vector",
        hits=[SearchHitPayload(
            chunk_id=UUID("33333333-3333-4333-8333-333333333333"),
            document_id=UUID("44444444-4444-4444-8444-444444444444"),
            document_name="公开 MCP 测试文档",
            chunk_index=1,
            content=PUBLIC_CONTENT,
            start_char=0,
            end_char=len(PUBLIC_CONTENT),
            distance=0.1,
        )],
    )


async def run(mode):
    if mode == "hang":
        await anyio.sleep_forever()
    await serve(create_server(public_search))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("healthy", "hang", "exit"), default="healthy")
    args = parser.parse_args()
    if args.mode == "exit":
        raise SystemExit(0)
    anyio.run(run, args.mode)
