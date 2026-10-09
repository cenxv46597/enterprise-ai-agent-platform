"""只读核对数据库文档记录与本地原文件，不推断归属或执行补偿。"""

import argparse
from dataclasses import asdict, dataclass
import json
import sys
from time import time
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Document
from app.db.session import get_session_factory
from app.knowledge.storage import LocalDocumentStore


@dataclass(frozen=True)
class StorageAudit:
    orphan_candidates: tuple[str, ...]
    recent_untracked: tuple[str, ...]
    missing_files: tuple[str, ...]


def compare_storage(
    store: LocalDocumentStore,
    storage_keys: Iterable[str],
    *,
    grace_seconds: int = 3600,
    now: float | None = None,
) -> StorageAudit:
    """按存储键对账；宽限期仅降低误报，不能证明上传已结束。"""

    if grace_seconds < 0:
        raise ValueError("宽限期不能为负数")
    cutoff = (time() if now is None else now) - grace_seconds
    referenced = set(storage_keys)
    published = store.published_files()
    untracked = published.keys() - referenced
    return StorageAudit(
        orphan_candidates=tuple(sorted(key for key in untracked if published[key] <= cutoff)),
        recent_untracked=tuple(sorted(key for key in untracked if published[key] > cutoff)),
        missing_files=tuple(sorted(referenced - published.keys())),
    )


def audit_document_storage(
    session: Session,
    store: LocalDocumentStore,
    *,
    grace_seconds: int = 3600,
    now: float | None = None,
) -> StorageAudit:
    """只读取全部文档的存储键，供本地维护命令使用。"""

    storage_keys = session.scalars(select(Document.storage_key)).all()
    return compare_storage(store, storage_keys, grace_seconds=grace_seconds, now=now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读核对文档记录和本地文件，输出 JSON 报告")
    parser.add_argument("--grace-seconds", type=int, default=3600, help="孤儿候选文件的宽限秒数，默认 3600")
    args = parser.parse_args(argv)
    if args.grace_seconds < 0:
        parser.error("宽限期不能为负数")
    try:
        settings = Settings()
        # 配错根目录应报错，不能创建空目录后把全部文档报告为丢失。
        if not (settings.upload_root / "documents").is_dir():
            raise FileNotFoundError("请确认上传目录已存在且 EAAP_UPLOAD_ROOT 配置正确")
        store = LocalDocumentStore(settings.upload_root)
        with get_session_factory()() as session:
            report = audit_document_storage(session, store, grace_seconds=args.grace_seconds)
    except SQLAlchemyError:
        print("对账失败：无法读取数据库，请检查数据库服务和迁移状态。", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError):
        print("对账失败：请检查数据库配置及上传目录是否存在、可读且不含非法链接。", file=sys.stderr)
        return 2

    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 1 if any(asdict(report).values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
