"""Fixed no-I/O checks for document acceptance and its reused listener/SQL guard."""

from pathlib import Path

from scripts.run_contract_tests import ContractSuiteAudit, ResourceGuard, isolated_settings, prepare_environment
from scripts.run_postgres_tests import invoke_pytest


BACKEND = Path(__file__).resolve().parents[1]
TEST_PATHS = (BACKEND / "tests/test_history_acceptance_guard.py",
              BACKEND / "tests/test_document_acceptance_guard.py")


class DocumentAcceptanceEntryAudit(ContractSuiteAudit):
    def pytest_collection_modifyitems(self, items):
        import pytest

        self.collected = len(items)
        if {Path(item.path).resolve() for item in items} != set(TEST_PATHS):
            raise pytest.UsageError("Document entry checks must collect both fixed safety files")
        self.collection_valid = True


def run_checks():
    prepare_environment()
    audit, guard = DocumentAcceptanceEntryAudit(), ResourceGuard()
    result, final = None, 1
    try:
        with isolated_settings(), guard:
            result = invoke_pytest(audit, TEST_PATHS)
        final = result if result or (audit.complete() and not guard.attempts) else 1
    except (Exception, KeyboardInterrupt) as failure:
        print(f"Document entry checks did not complete ({type(failure).__name__}).")
    print(f"Document entry checks: pytest_exit={result if result is not None else 'not-run'} "
          f"collected={audit.collected} passed={audit.passed} skipped={audit.skipped} "
          f"xfail={audit.xfailed} violations={len(guard.attempts)} final_exit={final}")
    return final
