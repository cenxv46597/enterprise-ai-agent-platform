"""通过本机 HTTP 后端登录并浏览资料；不读取配置、写入资料或调用模型。"""

import argparse
from dataclasses import dataclass
from getpass import GetPassWarning, getpass
import json
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID
import warnings

import httpx


PAGE_SIZE = 20
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


def request(client: httpx.Client, method: str, path: str, stage: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != 200:
        raise BrowseFailure(f"{stage}未完成（HTTP {response.status_code}）；请核对账号和后端状态。")
    try:
        return response.json()
    except ValueError:
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


def display(label: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        label = label.replace(secret, "[已隐藏]")
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


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description="登录本机后端并浏览自己的企业资料", allow_abbrev=False)
    parser.add_argument("--username", required=True, help="已注册用户名")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="本机后端地址")
    args = parser.parse_args(argv)
    username = args.username.strip().lower()
    try:
        base_url = local_origin(args.base_url)
        if re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username) is None:
            raise ValueError("invalid username")
    except ValueError:
        print("用户名或本机后端地址格式无效；请查看 --help。")
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
                return 0
            base_path = f"/api/v1/workspaces/{workspace.id}/knowledge-bases"
            knowledge_base = choose("知识库", entries(request(
                client, "GET", base_path, "读取知识库", params=page),
                parent_key="workspace_id", parent_id=workspace.id), secrets)
            if knowledge_base is None:
                return 0
            documents = entries(request(
                client, "GET", f"{base_path}/{knowledge_base.id}/documents", "读取文档", params=page),
                label_key="original_filename", parent_key="knowledge_base_id", parent_id=knowledge_base.id)
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
