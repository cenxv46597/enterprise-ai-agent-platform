"""Build the current frontend for local acceptance without loading .env files."""

import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    node = shutil.which("node")
    if not node:
        print("Frontend acceptance build requires the installed Node runtime.")
        return 1
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("EAAP_", "VITE_", "PYTEST_", "VITEST_"))
                   and key.upper() not in {"NODE_OPTIONS", "NODE_PATH"}}
    # Pass JavaScript as a subprocess argument, never through a shell or npm config.
    program = """
      import { spawnSync } from 'node:child_process';
      import path from 'node:path';
      import { pathToFileURL } from 'node:url';
      const root = ROOT;
      const checked = spawnSync(process.execPath, [path.join(root, 'node_modules/typescript/bin/tsc'), '--noEmit'], {
        cwd: root, env: process.env, stdio: 'inherit',
      });
      if (checked.status !== 0) process.exitCode = checked.status ?? 1;
      else {
        const { build } = await import(pathToFileURL(path.join(root, 'node_modules/vite/dist/node/index.js')).href);
        await build({ root, configFile: path.join(root, 'vite.config.ts'), envDir: false });
      }
    """.replace("ROOT", json.dumps(str(frontend)))
    result = subprocess.run([node, "--input-type=module", "--eval", program], cwd=frontend, env=environment)
    print(f"Frontend acceptance build: env_files_disabled=True final_exit={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
