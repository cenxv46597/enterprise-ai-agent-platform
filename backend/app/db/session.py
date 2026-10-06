"""为元数据接口按需创建同步数据库会话。"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = Settings().database_url
    if not database_url:
        raise RuntimeError("请先通过 EAAP_DATABASE_URL 配置数据库连接地址")
    return create_engine(database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """每个请求使用独立会话；写操作由调用方提交或回滚。"""
    with get_session_factory()() as session:
        yield session
