"""Fixed no-I/O checks for the isolated browser acceptance entry."""

from pathlib import Path

from scripts.run_contract_tests import ContractSuiteAudit, ResourceGuard, isolated_settings, prepare_environment
from scripts.run_postgres_tests import invoke_pytest


TEST_PATH = Path(__file__).resolve().parents[1] / "tests/test_history_acceptance_guard.py"


class AcceptanceEntryAudit(ContractSuiteAudit):
    def pytest_collection_modifyitems(self, items):
        import pytest

        self.collected = len(items)
        if {Path(item.path).resolve() for item in items} != {TEST_PATH}:
            raise pytest.UsageError("Browser entry checks must collect the fixed safety file")
        self.collection_valid = True


def run_checks():
    prepare_environment()
    audit, guard = AcceptanceEntryAudit(), ResourceGuard()
    result, final = None, 1
    try:
        with isolated_settings(), guard:
            result = invoke_pytest(audit, (TEST_PATH,))
        final = result if result or (audit.complete() and not guard.attempts) else 1
    except (Exception, KeyboardInterrupt) as failure:
        print(f"Browser entry checks did not complete ({type(failure).__name__}).")
    print(f"Browser entry checks: pytest_exit={result if result is not None else 'not-run'} "
          f"collected={audit.collected} passed={audit.passed} skipped={audit.skipped} "
          f"xfail={audit.xfailed} violations={len(guard.attempts)} final_exit={final}")
    return final
