"""Strip inherited settings before Node starts; use python -I scripts/check.py."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    mode = sys.argv[1:] or ["all"]
    if len(mode) != 1 or mode[0] not in {"all", "test", "build", "resources"}:
        print("Usage: python -I scripts/check.py [all|test|build|resources]", file=sys.stderr)
        return 2
    environment = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith(("EAAP_", "VITE_", "PYTEST_", "VITEST_"))
                   and name.upper() not in {"NODE_OPTIONS", "NODE_PATH"}}
    node = shutil.which("node")
    if not node:
        print("Node runtime unavailable", file=sys.stderr)
        return 1
    script = Path(__file__).resolve().with_name("check.mjs")
    try:
        result = subprocess.run([node, str(script), mode[0]], cwd=script.parent.parent,
                                env=environment, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        print("Frontend check exceeded its time budget", file=sys.stderr)
        return 1
    print(f"frontend_child_exit={result.returncode}", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
