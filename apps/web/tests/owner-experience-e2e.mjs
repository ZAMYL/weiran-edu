// Runs only isolated real servers owned by this test; never discovers/kills demo processes.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
const web = fileURLToPath(new URL('..', import.meta.url));
const repo = path.resolve(web, '../..');
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python);
const artifacts = path.join(web, 'artifacts/owner-experience');
await mkdir(artifacts, { recursive: true });
const env = {
  ...process.env,
  PYTHONUTF8: '1',
  PYTHONPATH: `${repo};${path.join(repo, 'apps/api')}`,
};
const result = { realQwen: 'not_run', executionOrigin: 'http_mock', stages: [] };
let host, connection;
async function run(script, extra = {}) {
  const label = extra.EDU_PRODUCT_FLOW
    ? 'teacher-product'
    : extra.EDU_INTEGER_FLOW
      ? 'integer-105'
      : 'ai-task';
  const child = spawn(process.execPath, [path.join(web, script)], {
    cwd: web,
    env: { ...env, ...extra },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  let output = '';
  child.stdout.on('data', (v) => {
    output += v;
  });
  child.stderr.on('data', (v) => {
    output += v;
  });
  const [code] = await once(child, 'exit');
  // Existing test scripts deliberately sanitize errors and never print credentials.
  await writeFile(path.join(artifacts, label + '.log'), output);
  result.stages.push({ label, script, code });
  assert.equal(code, 0, script + ' failed; see its artifact log');
}
try {
  await run('tests/discovery-api-flow.mjs', { EDU_PRODUCT_FLOW: '1' });
  await run('tests/discovery-api-flow.mjs', { EDU_INTEGER_FLOW: '1' });
  host = spawn(python, ['-u', path.join(web, 'tests/integration-synthetic-host.py')], {
    cwd: repo,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  let output = '';
  host.stdout.on('data', (v) => {
    output += v;
  });
  host.stderr.on('data', () => {});
  const deadline = Date.now() + 300000;
  while (!connection) {
    assert.equal(host.exitCode, null, 'synthetic host exited');
    assert.ok(Date.now() < deadline, 'synthetic host readiness timeout');
    for (const line of output.split('\n')) {
      try {
        const data = JSON.parse(line);
        if (data.ready) connection = data.connection;
      } catch {
        /* non-readiness line */
      }
    }
    if (!connection) await sleep(200);
  }
  const descriptor = JSON.parse(await readFile(connection, 'utf8'));
  assert.equal(descriptor.execution_origin, 'http_mock');
  await run('tests/ai-task-e2e.mjs', { EDU_AI_CONNECTION: connection });
  result.passed = true;
} catch (e) {
  result.passed = false;
  result.failure = e.message;
  process.exitCode = 1;
} finally {
  if (host?.exitCode === null && connection) {
    const stopped = once(host, 'exit');
    await writeFile(path.join(path.dirname(connection), 'stop.flag'), 'stop\n');
    await Promise.race([stopped, sleep(45000)]);
  }
  if (host?.exitCode === null) host.kill();
  await writeFile(path.join(artifacts, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
}
