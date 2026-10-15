"""通过本机 HTTP 后端浏览、入库、检索和基于资料问答。"""

import argparse
from dataclasses import dataclass
from getpass import GetPassWarning, getpass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID
import warnings

import httpx


PAGE_SIZE = 20
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MEDIA_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".pdf": "application/pdf"}
UPLOAD_UNCONFIRMED = "上传结果未确认；未自动重试。请先核对知识库中的文档记录，再决定是否再次上传。"
CHUNK_PARAMETERS = {"chunk_size": 500, "overlap": 50}
CHUNK_UNCONFIRMED = "切块结果未确认；未自动重试。请核对同一文档的已有文本块，同参数显式重试可复用结果。"
CHUNK_STATUS_UNCONFIRMED = "切块响应已校验，但最新文档状态未确认；未自动重试。请浏览同一文档核对状态。"
EMBEDDING_UNCONFIRMED = "向量化结果未确认；可能已调用模型或保存结果，未自动重试。请先浏览同一文档核对状态，不要立即重复提交。"
EMBEDDING_STATUS_UNCONFIRMED = "向量化成功响应已校验，但最新文档状态未确认；未自动重试。请浏览同一文档核对状态。"
EMBEDDING_TIMEOUT = httpx.Timeout(330, connect=10, write=10, pool=10)
SEARCH_TOP_K = 3
SEARCH_UNCONFIRMED = "检索结果未确认；本次查询可能已调用向量服务，未自动重试。请先核对后端日志，再决定是否重新查询。"
ANSWER_TIMEOUT = httpx.Timeout(630, connect=10, write=10, pool=10)
ANSWER_UNCONFIRMED = "问答结果未确认；本次可能已调用向量或生成服务，未自动重试。请先核对后端日志，再决定是否重新提问。"
STATUS_LABELS = {
    "uploaded": "已上传", "processing": "处理中", "parsed": "已解析",
    "ready": "可检索", "failed": "处理失败",
}


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse 的默认报错会回显误传的密码参数等内容。
        self.exit(2, "参数无效；请使用 --help 查看用法，密码只能在终端隐藏输入。\n")


class BrowseFailure(RuntimeError):
    """只携带客户端提供的固定提示，不保存响应正文或原始异常。"""


@dataclass(frozen=True)
class Entry:
    id: UUID
    label: str
    status: str | None = None


@dataclass(frozen=True)
class Upload:
    name: str
    content: bytes
    media_type: str


def load_upload(value: str) -> Upload:
    try:
        path = Path(value).expanduser()
        name = path.name
        if (not path.is_file() or path.suffix.lower() not in MEDIA_TYPES
                or len(name) > 255 or name != name.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise ValueError("invalid file")
        with path.open("rb") as source:
            content = source.read(MAX_UPLOAD_BYTES + 1)
        if not 1 <= len(content) <= MAX_UPLOAD_BYTES:
            raise ValueError("invalid size")
        return Upload(name, content, MEDIA_TYPES[path.suffix.lower()])
    except (OSError, ValueError, RuntimeError):
        raise BrowseFailure("上传文件无效；请选择非空、可读取且不超过 5 MiB 的 TXT、Markdown 或 PDF 常规文件。") from None


def local_origin(value: str) -> str:
    origin = urlsplit(value)
    port = origin.port if origin.port is not None else 80
    if (
        origin.scheme != "http"
        or origin.hostname not in {"127.0.0.1", "localhost", "::1"}
        or origin.username is not None or origin.password is not None
        or origin.path not in {"", "/"} or origin.query or origin.fragment
        or not 1 <= port <= 65535
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ValueError("invalid origin")
    return value.rstrip("/")


def request(client: httpx.Client, method: str, path: str, stage: str, *,
            expected_status: int = 200, may_create_document: bool = False,
            may_create_chunks: bool = False, may_create_embeddings: bool = False,
            may_embed_query: bool = False, may_answer: bool = False, **kwargs):
    uncertain_result = (ANSWER_UNCONFIRMED if may_answer else
                        SEARCH_UNCONFIRMED if may_embed_query else
                        EMBEDDING_UNCONFIRMED if may_create_embeddings else
                        CHUNK_UNCONFIRMED if may_create_chunks else
                        UPLOAD_UNCONFIRMED if may_create_document else None)
    try:
        response = client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        if uncertain_result:
            raise BrowseFailure(uncertain_result) from None
        raise BrowseFailure(
            f"{stage}等待后端响应超时；请检查 Docker 数据库是否已启动，并查看后端日志。"
        ) from None
    except httpx.ConnectError:
        if uncertain_result:
            raise BrowseFailure(uncertain_result) from None
        raise BrowseFailure(f"{stage}无法连接后端；请核对后端地址、端口和启动状态。") from None
    except httpx.RequestError:
        if uncertain_result:
            raise BrowseFailure(uncertain_result) from None
        raise BrowseFailure(f"{stage}网络请求失败；请检查本机连接和后端日志。") from None
    except KeyboardInterrupt:
        if uncertain_result:
            raise BrowseFailure(uncertain_result) from None
        raise
    if response.status_code != expected_status:
        if may_answer and response.status_code == 409:
            raise BrowseFailure("问答请求被拒绝（HTTP 409）：查询模型与知识库向量配置不一致；未自动重试。")
        if may_embed_query and response.status_code == 409:
            raise BrowseFailure("检索请求被拒绝（HTTP 409）：查询模型与知识库向量配置不一致；未自动重试。")
        if may_create_embeddings and response.status_code in {409, 422}:
            raise BrowseFailure(
                f"向量化请求被拒绝（HTTP {response.status_code}）：请核对已有文本块、向量配置及本次块数上限；未自动重试。"
            )
        if may_create_chunks and response.status_code == 409:
            raise BrowseFailure("切块请求被拒绝（HTTP 409）：文档尚未解析或已有配置不兼容；请核对文档和原切块配置。")
        if uncertain_result and not 400 <= response.status_code < 500:
            raise BrowseFailure(uncertain_result)
        raise BrowseFailure(f"{stage}未完成（HTTP {response.status_code}）；请核对账号和后端状态。")
    try:
        return response.json()
    except ValueError:
        if uncertain_result:
            raise BrowseFailure(uncertain_result) from None
        raise BrowseFailure(f"{stage}返回了无效数据。") from None


def entries(payload, *, label_key: str = "name", parent_key: str | None = None,
            parent_id: UUID | None = None) -> list[Entry]:
    if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
        raise BrowseFailure("列表数据格式不符合预期。")
    rows = []
    for item in payload:
        try:
            identifier = UUID(item["id"])
            label = item[label_key]
            if not isinstance(label, str) or not 1 <= len(label) <= 300:
                raise ValueError("invalid label")
            if parent_key and UUID(item[parent_key]) != parent_id:
                raise ValueError("wrong parent")
            state = item["status"] if label_key == "original_filename" else None
            if label_key == "original_filename" and state not in STATUS_LABELS:
                raise ValueError("invalid status")
            rows.append(Entry(identifier, label, state))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise BrowseFailure("列表数据格式或资源归属不符合预期。") from None
    if len({row.id for row in rows}) != len(rows):
        raise BrowseFailure("列表出现重复编号，无法安全选择。")
    return rows


def display(label: str, secrets: tuple[str, ...], *, max_characters: int | None = None) -> str:
    for secret in secrets:
        label = label.replace(secret, "[已隐藏]")
    # 必须先隐藏完整凭据再截取摘要，避免只显示跨截断边界的部分凭据。
    if max_characters is not None and len(label) > max_characters:
        label = label[:max_characters] + "…"
    # 转义换行及终端控制字符，文件名不会伪装成另一行提示。
    return json.dumps(label, ensure_ascii=False)


def show(title: str, rows: list[Entry], secrets: tuple[str, ...]) -> None:
    print(f"\n{title}（第一页，最多 {PAGE_SIZE} 条）：")
    for number, row in enumerate(rows, 1):
        state = f" — {STATUS_LABELS[row.status]}" if row.status else ""
        print(f"{number}. {display(row.label, secrets)}{state}")
    if len(rows) == PAGE_SIZE:
        print("当前页已满，可能还有更多记录；本次只展示第一页。")


def choose(title: str, rows: list[Entry], secrets: tuple[str, ...]) -> Entry | None:
    show(title, rows, secrets)
    if not rows:
        print("列表为空，本次浏览结束。")
        return None
    number = input("请选择编号：").strip()
    if re.fullmatch(r"[1-9][0-9]?", number) is None or int(number) > len(rows):
        raise BrowseFailure("选择无效；请重新运行并选择显示列表中的编号。")
    return rows[int(number) - 1]


def upload_document(client: httpx.Client, url: str, knowledge_base_id: UUID,
                    upload: Upload, secrets: tuple[str, ...]) -> bool:
    payload = request(client, "POST", url, "上传文件", expected_status=201, may_create_document=True,
                      files={"file": (upload.name, upload.content, upload.media_type)})
    try:
        document = entries([payload], label_key="original_filename",
                           parent_key="knowledge_base_id", parent_id=knowledge_base_id)[0]
        if (document.label != upload.name or type(payload["byte_size"]) is not int
                or payload["byte_size"] != len(upload.content)
                or payload["sha256"] != sha256(upload.content).hexdigest()
                or document.status not in {"parsed", "failed"}):
            raise ValueError("unexpected uploaded document")
        error_code = payload["error_code"]
        if ((document.status == "parsed" and error_code is not None)
                or (document.status == "failed" and (not isinstance(error_code, str) or not error_code))):
            raise ValueError("unexpected parse result")
    except (BrowseFailure, KeyError, TypeError, ValueError, AttributeError):
        raise BrowseFailure(UPLOAD_UNCONFIRMED) from None
    if document.status == "failed":
        reason = {"EMPTY_TEXT": "未提取到文字", "INVALID_UTF8": "文本不是 UTF-8 编码",
                  "STORAGE_READ_FAILED": "原文件读取失败"}.get(error_code, "未知解析错误")
        print(f"文件记录已创建：{display(document.label, secrets)} — 处理失败（{reason}）。")
        return False
    print(f"文件记录已创建：{display(document.label, secrets)} — 已解析，尚未可检索。")
    return True


def chunk_document(client: httpx.Client, document_url: str, knowledge_base_id: UUID,
                   document: Entry, secrets: tuple[str, ...]) -> bool:
    if document.status not in {"parsed", "ready"}:
        raise BrowseFailure("文档尚未成功解析，本次未切块。")
    payload = request(client, "POST", document_url + "/chunks", "文档切块",
                      json=CHUNK_PARAMETERS, may_create_chunks=True)
    try:
        if not isinstance(payload, list) or not 1 <= len(payload) <= 10_000:
            raise ValueError("invalid chunk list")
        identifiers = set()
        spans = []
        total_characters = 0
        for number, item in enumerate(payload, 1):
            identifier = UUID(item["id"])
            start, end, content = item["start_char"], item["end_char"], item["content"]
            if (identifier in identifiers or UUID(item["document_id"]) != document.id
                    or type(item["chunk_index"]) is not int or item["chunk_index"] != number
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end or not isinstance(content, str) or len(content) != end - start):
                raise ValueError("invalid chunk identity or span")
            total_characters += len(content)
            if total_characters > 10 * 1024 * 1024:
                raise ValueError("chunk response exceeds budget")
            identifiers.add(identifier)
            spans.append((number, start, end))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise BrowseFailure(CHUNK_UNCONFIRMED) from None
    print(f"切块请求成功：{display(document.label, secrets)}，共 {len(spans)} 个文本块。")
    for number, start, end in spans[:3]:
        print(f"第 {number} 块：[{start}, {end})")
    if len(spans) > 3:
        print("仅展示前 3 块的字符范围。")
    try:
        latest_payload = request(client, "GET", document_url, "读取文档最新状态")
        latest = entries([latest_payload], label_key="original_filename",
                         parent_key="knowledge_base_id", parent_id=knowledge_base_id)[0]
        if latest.id != document.id or latest.label != document.label:
            raise ValueError("unexpected document identity")
    except (BrowseFailure, KeyError, TypeError, ValueError, AttributeError, KeyboardInterrupt, EOFError):
        raise BrowseFailure(CHUNK_STATUS_UNCONFIRMED) from None
    state = "已解析，尚未可检索" if latest.status == "parsed" else STATUS_LABELS[latest.status]
    print(f"文档当前状态：{state}。")
    return latest.status in {"parsed", "ready"}


def embed_document(client: httpx.Client, document_url: str, knowledge_base_id: UUID,
                   document: Entry, max_chunks: int, secrets: tuple[str, ...]) -> bool:
    if document.status not in {"parsed", "ready"}:
        raise BrowseFailure("文档尚未成功解析，本次未提交向量化。")
    print(f"向量化 {display(document.label, secrets)}：本次新生成最多 {max_chunks} 个块，不自动重试。")
    payload = request(client, "POST", document_url + "/embeddings", "文档向量化",
                      json={"max_chunks": max_chunks}, timeout=EMBEDDING_TIMEOUT,
                      may_create_embeddings=True)
    try:
        count, reused = payload["chunk_count"], payload["reused"]
        model = payload["model"]
        if (UUID(payload["document_id"]) != document.id or payload["status"] != "ready"
                or type(payload["dimensions"]) is not int or payload["dimensions"] != 1024
                or type(count) is not int or not 1 <= count <= 10_000
                or type(reused) is not bool or (not reused and count > max_chunks)
                or not isinstance(model, str) or not 1 <= len(model.strip()) <= 300):
            raise ValueError("unexpected embedding result")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise BrowseFailure(EMBEDDING_UNCONFIRMED) from None
    result = "复用已保存的结果" if reused else "结果已保存"
    print(f"向量化请求成功：{count} 个块，1024 维，{result}。")
    try:
        latest_payload = request(client, "GET", document_url, "读取文档最新状态")
        latest = entries([latest_payload], label_key="original_filename",
                         parent_key="knowledge_base_id", parent_id=knowledge_base_id)[0]
        if latest.id != document.id or latest.label != document.label:
            raise ValueError("unexpected document identity")
    except (BrowseFailure, KeyError, TypeError, ValueError, AttributeError, KeyboardInterrupt, EOFError):
        raise BrowseFailure(EMBEDDING_STATUS_UNCONFIRMED) from None
    print(f"文档当前状态：{STATUS_LABELS[latest.status]}。")
    return latest.status == "ready"


def verify_source_documents(client: httpx.Client, base_url: str, knowledge_base_id: UUID,
                            documents: dict[UUID, str]) -> None:
    for document_id, name in documents.items():
        detail = request(client, "GET", f"{base_url}/documents/{document_id}", "核对来源文档")
        document = entries([detail], label_key="original_filename",
                           parent_key="knowledge_base_id", parent_id=knowledge_base_id)[0]
        if document.id != document_id or document.label != name or document.status != "ready":
            raise ValueError("unexpected source document")


def search_knowledge_base(client: httpx.Client, base_url: str, knowledge_base_id: UUID,
                          secrets: tuple[str, ...]) -> bool:
    query = input("请输入检索问题（1..2000 字符）：").strip()
    if not 1 <= len(query) <= 2000:
        raise BrowseFailure("检索问题为空或超过 2000 字符，本次未提交检索。")
    payload = request(client, "POST", base_url + "/search", "知识库检索",
                      json={"query": query, "top_k": SEARCH_TOP_K, "strategy": "vector", "rerank": False},
                      timeout=EMBEDDING_TIMEOUT, may_embed_query=True)
    try:
        hits = payload["results"]
        if (payload["metric"] != "cosine_distance" or payload["strategy"] != "vector"
                or payload.get("reranking") is not None
                or not isinstance(hits, list) or len(hits) > SEARCH_TOP_K):
            raise ValueError("unexpected search mode or count")
        model, dimensions = payload["model"], payload["dimensions"]
        if not (not hits and model is None and dimensions is None):
            if (not isinstance(model, str) or not 1 <= len(model.strip()) <= 300
                    or type(dimensions) is not int or dimensions != 1024):
                raise ValueError("unexpected query embedding profile")
        seen, documents = set(), {}
        total_characters, previous_distance = 0, -1.0
        for hit in hits:
            identifier, document_id = UUID(hit["chunk_id"]), UUID(hit["document_id"])
            name, content = hit["document_name"], hit["content"]
            start, end, distance = hit["start_char"], hit["end_char"], hit["distance"]
            if (identifier in seen or not isinstance(name, str) or not 1 <= len(name) <= 300
                    or type(hit["chunk_index"]) is not int or hit["chunk_index"] < 1
                    or type(start) is not int or type(end) is not int or not 0 <= start < end
                    or not isinstance(content, str) or len(content) != end - start
                    or type(distance) not in {int, float} or not math.isfinite(distance)
                    or not 0 <= distance <= 2 or distance < previous_distance
                    or (document_id in documents and documents[document_id] != name)):
                raise ValueError("unexpected hit identity, content or distance")
            seen.add(identifier)
            documents[document_id] = name
            total_characters += len(content)
            if total_characters > 100_000:
                raise ValueError("search response exceeds display budget")
            previous_distance = distance
        # 先核对全部命中文档的父级，再展示正文；同一文档仅读取一次。
        verify_source_documents(client, base_url, knowledge_base_id, documents)
    except (BrowseFailure, KeyError, TypeError, ValueError, AttributeError, KeyboardInterrupt, EOFError):
        raise BrowseFailure(SEARCH_UNCONFIRMED) from None
    print(f"检索完成：返回 {len(hits)} 个候选段落。")
    if not hits:
        print("候选为空，本次没有返回可用段落。")
        return True
    print("以下为候选原文，每段最多展示 300 字符；余弦距离越小越接近，不是答案正确率。")
    for number, hit in enumerate(hits, 1):
        print(f"{number}. {display(hit['document_name'], secrets)} — 第 {hit['chunk_index']} 块，"
              f"[{hit['start_char']}, {hit['end_char']})，余弦距离 {hit['distance']:.4f}")
        print(f"原文摘录：{display(hit['content'], secrets, max_characters=300)}")
    return True


def answer_knowledge_base(client: httpx.Client, base_url: str, knowledge_base_id: UUID,
                          secrets: tuple[str, ...]) -> bool:
    question = input("请输入问答问题（1..2000 字符）：").strip()
    if not 1 <= len(question) <= 2000:
        raise BrowseFailure("问题为空或超过 2000 字符，本次未提交问答。")
    payload = request(client, "POST", base_url + "/answers", "资料问答",
                      json={"question": question, "top_k": SEARCH_TOP_K, "strategy": "vector", "rerank": False},
                      timeout=ANSWER_TIMEOUT, may_answer=True)
    try:
        answer, abstained, citations = payload["answer"], payload["abstained"], payload["citations"]
        if (not isinstance(answer, str) or not answer.strip() or len(answer) > 4000
                or type(abstained) is not bool or not isinstance(citations, list) or len(citations) > 20
                or (abstained and citations) or (not abstained and not citations)
                or payload.get("reranking") is not None):
            raise ValueError("unexpected answer decision")
        seen, documents, chunks = set(), {}, {}
        for citation in citations:
            chunk_id, document_id = UUID(citation["chunk_id"]), UUID(citation["document_id"])
            name, index, quote = citation["document_name"], citation["chunk_index"], citation["quote"]
            start, end = citation["start_char"], citation["end_char"]
            if (not isinstance(name, str) or not 1 <= len(name) <= 300
                    or type(index) is not int or index < 1
                    or not isinstance(quote, str) or not quote.strip() or len(quote) > 1000
                    or type(start) is not int or type(end) is not int or not 0 <= start < end
                    or end - start != len(quote) or (chunk_id, quote) in seen
                    or (document_id in documents and documents[document_id] != name)
                    or (chunk_id in chunks and chunks[chunk_id] != (document_id, index))):
                raise ValueError("unexpected citation identity or range")
            seen.add((chunk_id, quote))
            documents[document_id] = name
            chunks[chunk_id] = (document_id, index)
        verify_source_documents(client, base_url, knowledge_base_id, documents)
    except (BrowseFailure, KeyError, TypeError, ValueError, AttributeError, KeyboardInterrupt, EOFError):
        raise BrowseFailure(ANSWER_UNCONFIRMED) from None
    if abstained:
        print("证据不足，本次弃答。")
        return True
    print(f"回答：{display(answer, secrets)}")
    for number, citation in enumerate(citations, 1):
        print(f"引用 {number}：{display(citation['document_name'], secrets)} — 第 {citation['chunk_index']} 块，"
              f"引文位置 [{citation['start_char']}, {citation['end_char']})")
        print(f"引文：{display(citation['quote'], secrets)}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description="登录本机后端并浏览自己的企业资料", allow_abbrev=False)
    parser.add_argument("--username", required=True, help="已注册用户名")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="本机后端地址")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--upload", metavar="FILE", help="显式上传一份本机文件到选中的知识库，最多 5 MiB")
    actions.add_argument("--chunk", action="store_true", help="选择一份已有文档，以 500 字符、50 字符重叠保存切块")
    actions.add_argument("--embed", action="store_true", help="向量化一份已有文档；可能调用付费模型，必须同时指定 --max-chunks")
    actions.add_argument("--search", action="store_true", help="输入一个问题，检索选定知识库的最多 3 个候选段落；可能调用向量模型")
    actions.add_argument("--ask", action="store_true", help="输入一个问题，展示有引用的资料回答或弃答；可能调用向量和生成模型")
    parser.add_argument("--max-chunks", type=int, help="本次新生成向量的文本块上限（1..1000），仅用于 --embed")
    args = parser.parse_args(argv)
    if (args.embed != (args.max_chunks is not None)
            or (args.max_chunks is not None and not 1 <= args.max_chunks <= 1000)):
        parser.error("embedding requires an explicit valid chunk limit")
    username = args.username.strip().lower()
    try:
        base_url = local_origin(args.base_url)
        if re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username) is None:
            raise ValueError("invalid username")
    except ValueError:
        print("用户名或本机后端地址格式无效；请查看 --help。")
        return 2
    try:
        upload = load_upload(args.upload) if args.upload is not None else None
    except BrowseFailure as error:
        print(str(error))
        return 2
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("请在本机交互终端运行，以便隐藏输入密码和选择资料。")
        return 2
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("登录密码（输入隐藏）：")
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        print("密码输入已中止；没有发起请求。")
        return 2
    except Exception:
        print("无法安全读取密码；没有发起请求。")
        return 2
    if not 1 <= len(password) <= 128:
        print("密码长度无效；没有发起请求。")
        return 2

    try:
        # 不接受代理环境变量，也不向重定向地址发送凭据。
        with httpx.Client(base_url=base_url, timeout=10, trust_env=False,
                          follow_redirects=False) as client:
            auth = request(client, "POST", "/api/v1/auth/login", "登录",
                           json={"username": username, "password": password})
            token = auth["access_token"]
            if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                    or auth["token_type"] != "bearer"
                    or re.fullmatch(r"[A-Za-z0-9._~-]+", token) is None):
                raise BrowseFailure("登录结果格式不符合预期。")
            client.headers["Authorization"] = f"Bearer {token}"
            me = request(client, "GET", "/api/v1/auth/me", "核对身份")
            UUID(me["id"])
            if me["username"] != username:
                raise BrowseFailure("登录身份不符合预期，已停止浏览。")
            secrets = (password, token)
            print(f"已登录：{display(username, secrets)}")
            page = {"limit": PAGE_SIZE, "offset": 0}
            workspace = choose("工作空间", entries(request(
                client, "GET", "/api/v1/workspaces", "读取工作空间", params=page)), secrets)
            if workspace is None:
                if upload is not None:
                    print("没有可选工作空间，本次未上传文件。")
                    return 1
                if args.chunk:
                    print("没有可选工作空间，本次未切块。")
                    return 1
                if args.embed:
                    print("没有可选工作空间，本次未提交向量化。")
                    return 1
                if args.search:
                    print("没有可选工作空间，本次未提交检索。")
                    return 1
                if args.ask:
                    print("没有可选工作空间，本次未提交问答。")
                    return 1
                return 0
            base_path = f"/api/v1/workspaces/{workspace.id}/knowledge-bases"
            knowledge_base = choose("知识库", entries(request(
                client, "GET", base_path, "读取知识库", params=page),
                parent_key="workspace_id", parent_id=workspace.id), secrets)
            if knowledge_base is None:
                if upload is not None:
                    print("没有可选知识库，本次未上传文件。")
                    return 1
                if args.chunk:
                    print("没有可选知识库，本次未切块。")
                    return 1
                if args.embed:
                    print("没有可选知识库，本次未提交向量化。")
                    return 1
                if args.search:
                    print("没有可选知识库，本次未提交检索。")
                    return 1
                if args.ask:
                    print("没有可选知识库，本次未提交问答。")
                    return 1
                return 0
            if args.ask:
                selected_base_url = f"{base_path}/{knowledge_base.id}"
                return 0 if answer_knowledge_base(client, selected_base_url, knowledge_base.id, secrets) else 1
            if args.search:
                selected_base_url = f"{base_path}/{knowledge_base.id}"
                return 0 if search_knowledge_base(client, selected_base_url, knowledge_base.id, secrets) else 1
            document_url = f"{base_path}/{knowledge_base.id}/documents"
            if upload is not None:
                print(f"向知识库 {display(knowledge_base.label, secrets)} 上传 {display(upload.name, secrets)}。")
                if not upload_document(client, document_url, knowledge_base.id, upload, secrets):
                    return 1
            documents = entries(request(
                client, "GET", document_url, "读取文档", params=page),
                label_key="original_filename", parent_key="knowledge_base_id", parent_id=knowledge_base.id)
            if args.chunk or args.embed:
                selected = choose("选择要向量化的文档" if args.embed else "选择要切块的文档", documents, secrets)
                if selected is None:
                    print("没有可选文档，本次未提交向量化。" if args.embed else "没有可选文档，本次未切块。")
                    return 1
                selected_url = f"{document_url}/{selected.id}"
                if args.embed:
                    return 0 if embed_document(client, selected_url, knowledge_base.id,
                                               selected, args.max_chunks, secrets) else 1
                return 0 if chunk_document(client, selected_url, knowledge_base.id, selected, secrets) else 1
            show("文档与处理状态", documents, secrets)
            print("浏览完成。" if documents else "列表为空，本次浏览结束。")
            return 0
    except BrowseFailure as error:
        print(str(error))
    except (EOFError, KeyboardInterrupt):
        print("浏览已中止。")
        return 2
    except Exception:
        # 连接错误、响应结构等异常可能包含凭据，不能回显原文。
        print("无法完成浏览；请检查后端是否启动，以及账号和返回数据是否有效。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
