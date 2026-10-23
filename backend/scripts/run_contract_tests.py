"""Run the fixed HTTP contract suite without private settings, networks or databases.

Invoke this file with Python from any working directory. No pytest arguments are
accepted: database and model suites require a separate, explicitly chosen entry.
The guards prevent accidental I/O in this suite; they are not a process sandbox.
"""

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import local
from unittest.mock import patch
from weakref import WeakSet


BACKEND = Path(__file__).resolve().parents[1]
TEST_NAMES = (
    "test_health.py",
    "test_readiness.py",
    "test_database_readiness.py",
    "test_web_session.py",
    "test_http_trace.py",
    "test_http_trace_asgi.py",
    "test_http_trace_app.py",
    "test_http_trace_metadata.py",
    "test_answer_error_trace.py",
    "test_answer_workflow_listing.py",
    "test_document_chunk_read.py",
    "test_document_listing.py",
    "test_contract_runner.py",
)
TEST_PATHS = tuple(BACKEND / "tests" / name for name in TEST_NAMES)


def prepare_environment() -> None:
    """Remove inherited settings by name, without reading or printing their values."""
    for name in tuple(os.environ):
        if name.upper().startswith(("EAAP_", "PYTEST_")):
            del os.environ[name]
    os.environ["EAAP_APP_ENV"] = "test"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"


@contextmanager
def isolated_settings():
    from app.core.config import Settings
    from pydantic_settings import DotEnvSettingsSource, EnvSettingsSource, SecretsSettingsSource

    def sources(_cls, _settings_cls, init_settings, env_settings,
                dotenv_settings, file_secret_settings):
        # Explicit synthetic inputs remain usable, including production validators.
        return init_settings, lambda: {"app_env": "test"}

    with ExitStack() as stack:
        stack.enter_context(patch.dict(Settings.model_config, env_file=None, secrets_dir=None))
        stack.enter_context(patch.object(Settings, "settings_customise_sources", classmethod(sources)))
        stack.enter_context(patch.object(EnvSettingsSource, "_load_env_vars", lambda _self: {}))
        # Pydantic constructs dotenv sources before settings_customise_sources.
        # Disable the read itself, including an explicit _env_file override.
        stack.enter_context(patch.object(DotEnvSettingsSource, "_read_env_files", lambda _self: {}))
        stack.enter_context(patch.object(SecretsSettingsSource, "__call__", lambda _self: {}))
        yield


class ResourceAccessBlocked(RuntimeError):
    """A prohibited operation was stopped before dispatch; arguments stay private."""


class ResourceGuard:
    """Block accidental network/DB I/O and remember even swallowed violations."""

    def __init__(self):
        self.attempts = []
        self._socketpair_state = local()
        self._socketpairs = WeakSet()
        self._patches = ExitStack()

    def _reject(self, category):
        self.attempts.append(category)
        raise ResourceAccessBlocked(f"Contract suite forbids {category}")

    def _denied(self, category):
        def denied(*_args, **_kwargs):
            self._reject(category)
        return denied

    def _socket_operation(self, original, *, internal_send=False):
        def guarded(sock, *_args, **_kwargs):
            if (getattr(self._socketpair_state, "active", False)
                    or (internal_send and sock in self._socketpairs)):
                return original(sock, *_args, **_kwargs)
            self._reject("socket I/O")
        return guarded

    def __enter__(self):
        import asyncio
        from asyncio import proactor_events, selector_events
        import httpx
        import psycopg
        from sqlalchemy.engine import Engine
        import sqlite3

        def install(owner, name, replacement):
            self._patches.enter_context(patch.object(owner, name, replacement))

        original_socketpair = socket.socketpair

        def internal_socketpair(*args, **kwargs):
            # Windows can implement the event loop's wakeup pair with loopback.
            # Only this synchronous helper may create that pair, in this thread.
            previous = getattr(self._socketpair_state, "active", False)
            self._socketpair_state.active = True
            try:
                pair = original_socketpair(*args, **kwargs)
                self._socketpairs.update(pair)
                return pair
            finally:
                self._socketpair_state.active = previous

        async def async_network_denied(*_args, **_kwargs):
            self._reject("async network I/O")

        try:
            install(socket, "socketpair", internal_socketpair)
            for name in ("connect", "connect_ex", "bind", "listen", "send", "sendall", "sendto", "sendmsg"):
                if hasattr(socket.socket, name):
                    install(socket.socket, name, self._socket_operation(
                        getattr(socket.socket, name), internal_send=name.startswith("send"),
                    ))
            for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
                install(socket, name, self._denied("network resolution or connection"))
            # Windows ConnectEx can bypass socket.socket.connect.
            for name in ("create_connection", "create_server", "create_datagram_endpoint"):
                install(asyncio.BaseEventLoop, name, async_network_denied)
            for loop_class in (selector_events.BaseSelectorEventLoop, proactor_events.BaseProactorEventLoop):
                install(loop_class, "sock_connect", async_network_denied)
            install(httpx.HTTPTransport, "handle_request", self._denied("HTTP network transport"))
            install(httpx.AsyncHTTPTransport, "handle_async_request", async_network_denied)
            install(psycopg, "connect", self._denied("database connection"))
            install(psycopg.Connection, "connect", classmethod(self._denied("database connection")))
            install(psycopg.AsyncConnection, "connect", classmethod(async_network_denied))
            install(Engine, "connect", self._denied("database connection"))
            install(Engine, "raw_connection", self._denied("database connection"))
            install(sqlite3, "connect", self._denied("database connection"))
            return self
        except BaseException:
            self._patches.close()
            raise

    def __exit__(self, *_exc):
        self._patches.close()


class ContractSuiteAudit:
    def __init__(self):
        self.collected = 0
        self.passed = 0
        self.skipped = 0
        self.xfailed = 0
        self.collection_valid = False

    def pytest_collection_modifyitems(self, items):
        import pytest

        expected = {path.resolve() for path in TEST_PATHS}
        collected = {Path(item.path).resolve() for item in items}
        self.collected = len(items)
        if collected != expected:
            raise pytest.UsageError("Contract suite collection must match the fixed file whitelist")
        self.collection_valid = True

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped += 1
        if hasattr(report, "wasxfail"):
            self.xfailed += 1
        elif report.when == "call" and report.passed:
            self.passed += 1

    def complete(self):
        return (self.collection_valid and self.collected > 0 and self.passed == self.collected
                and not self.skipped and not self.xfailed)


def _invoke_pytest(audit) -> int:
    import pytest

    # Avoid pytest's shared per-user temp directory and any stale local ACLs.
    # This context owns only a newly created directory, never an existing path.
    with TemporaryDirectory(prefix="eaap-contract-") as temporary:
        return int(pytest.main([
            "-q", "-c", os.devnull, "--rootdir", str(BACKEND), "--noconftest",
            "--basetemp", str(Path(temporary) / "pytest"),
            "-p", "no:cacheprovider", "--tb=short", "--strict-config", "--strict-markers",
            *(str(path) for path in TEST_PATHS),
        ], plugins=[audit]))


def main(argv=None) -> int:
    if (sys.argv[1:] if argv is None else argv):
        print("This runner accepts no arguments; its contract test whitelist is fixed.", file=sys.stderr)
        return 2
    prepare_environment()
    sys.path.insert(0, str(BACKEND))
    guard = ResourceGuard()
    audit = ContractSuiteAudit()
    pytest_exit = None
    final_exit = 1
    try:
        with isolated_settings(), guard:
            pytest_exit = _invoke_pytest(audit)
        final_exit = pytest_exit
        if guard.attempts or (pytest_exit == 0 and not audit.complete()):
            final_exit = 1
    except (Exception, KeyboardInterrupt) as failure:
        print(f"Contract suite did not complete ({type(failure).__name__}).", file=sys.stderr)
    if guard.attempts:
        print("Contract suite failed: prohibited network or database access was attempted.", file=sys.stderr)
    print(f"Contract suite: pytest_exit={pytest_exit if pytest_exit is not None else 'not-run'} "
          f"collected={audit.collected} passed={audit.passed} skipped={audit.skipped} "
          f"xfail={audit.xfailed} violations={len(guard.attempts)} final_exit={final_exit}")
    return final_exit


if __name__ == "__main__":
    raise SystemExit(main())
