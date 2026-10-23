// Reproducible local/CI checks. Disable private environment-file loading before importing Vite.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const mode = process.argv[2] ?? 'all';
if (process.argv.length > 3 || !['all', 'test', 'build', 'resources'].includes(mode)) {
  console.error('Usage: node scripts/check.mjs [all|test|build|resources]');
  process.exit(2);
}
for (const name of Object.keys(process.env)) {
  if (/^(EAAP_|VITE_|PYTEST_|VITEST_)/i.test(name) || /^(NODE_OPTIONS|NODE_PATH)$/i.test(name)) delete process.env[name];
}
const parent = path.resolve(os.tmpdir());
const temporary = fs.mkdtempSync(path.join(parent, 'eaap-frontend-'));
process.env.LOCALAPPDATA = path.join(temporary, 'local');
fs.mkdirSync(process.env.LOCALAPPDATA);
process.chdir(root);
const imported = relative => import(pathToFileURL(path.join(root, relative)).href);
try {
  if (mode !== 'build') {
    const { startVitest } = await imported('node_modules/vitest/dist/node.js');
    const filters = mode === 'resources' ? ['src/resource-api.test.ts', 'src/resources.test.tsx'] : [];
    const context = await startVitest(filters, {
      root, config: path.join(root, 'vite.config.ts'), watch: false, run: true,
      reporters: ['dot'], maxWorkers: 2,
    }, { envDir: false });
    if (!context) process.exitCode = 1;
    else {
      if (context.state.getFiles().length === 0) process.exitCode = 1;
      await context.close();
    }
  }
  if (mode === 'all' && !process.exitCode) {
    const environment = { ...process.env, NODE_ENV: 'production' };
    for (const name of Object.keys(environment)) if (/^(VITE_|VITEST_)/i.test(name)) delete environment[name];
    const built = spawnSync(process.execPath, [fileURLToPath(import.meta.url), 'build'], {
      cwd: root, stdio: 'inherit', env: environment,
    });
    process.exitCode = built.status ?? 1;
  }
  if (mode === 'build' && !process.exitCode) {
    process.env.NODE_ENV = 'production';
    const checked = spawnSync(process.execPath, ['node_modules/typescript/bin/tsc', '--noEmit'], {
      cwd: root, stdio: 'inherit', env: process.env,
    });
    if (checked.status !== 0) process.exitCode = checked.status ?? 1;
    else {
      const { build } = await imported('node_modules/vite/dist/node/index.js');
      await build({ root, configFile: path.join(root, 'vite.config.ts'), envDir: false });
    }
  }
} catch (failure) {
  process.exitCode = 1;
  console.error(`Frontend check failed (${failure instanceof Error ? failure.name : 'unknown error'}).`);
} finally {
  const actual = path.resolve(temporary);
  if (path.dirname(actual) !== parent || !path.basename(actual).startsWith('eaap-frontend-')) {
    throw new Error('Temporary directory ownership not confirmed');
  }
  fs.rmSync(actual, { recursive: true });
  console.log(`frontend_check=${mode}; env_files_disabled=true; temporary_profile_removed=true; final_exit=${process.exitCode ?? 0}`);
}
