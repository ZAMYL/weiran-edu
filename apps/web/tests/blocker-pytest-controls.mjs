// Explicit bounded load for the two previously failed tests; no deadline or budget changes.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const web = fileURLToPath(new URL('..', import.meta.url));
const repo = path.resolve(web, '../..');
const python = process.env.EDU_TEST_PYTHON;
const loadPython = process.env.EDU_LOAD_PYTHON;
assert.ok(python && loadPython);
const round = process.env.EDU_PYTEST_ROUND || 'pytest-controls';
assert.match(round, /^[a-z0-9-]+$/);
const directory = path.join(web, 'artifacts/504-diagnosis', round);
await mkdir(directory, { recursive: true });
const tests = [
  'tests/durable_teacher_agent/test_tasks.py::test_persist_reload_evidence_review_and_report',
  'tests/durable_teacher_agent/test_priority_questions.py::test_teacher_priority_alias_rejects_unsafe_text_and_reuses_controlled_task',
];
const fullTests = [
  'tests/edu/test_discovery_api.py',
  'tests/edu/test_discovery_bridge.py',
  'tests/edu/test_discovery_worker.py',
  'tests/edu/test_discovery_reuse.py',
  'tests/edu/test_discovery_results.py',
  'tests/edu/test_discovery_report_limits.py',
  'tests/durable_teacher_agent/test_tasks.py',
  'tests/durable_teacher_agent/test_priority_questions.py',
];
const runs = [];
const loadCounts = (process.env.EDU_PYTEST_LOADS || '0,2,2,2').split(',').map(Number);
assert.ok(loadCounts.length && loadCounts.every((count) => [0, 2].includes(count)));
for (const loadCount of loadCounts) {
  const name = `${runs.length + 1}-cpu${loadCount}`;
  const load = Array.from({ length: loadCount }, () =>
    spawn(
      loadPython,
      [
        '-c',
        'import math,time; end=time.monotonic()+240\nwhile time.monotonic()<end:\n for i in range(100000): math.sqrt(i)',
      ],
      { stdio: 'ignore', windowsHide: true },
    ),
  );
  const started = performance.now();
  const child = spawn(
    python,
    [
      '-u',
      '-m',
      'pytest',
      '-q',
      ...(loadCount === 0 && process.env.EDU_PYTEST_FULL === '1' ? fullTests : tests),
      '--durations=5',
      `--junitxml=${path.join(directory, name + '.xml')}`,
    ],
    {
      cwd: path.join(repo, 'apps/api'),
      env: {
        ...process.env,
        PYTHONPATH: `${repo};${path.join(repo, 'apps/api')}`,
        PYTHONUTF8: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  let output = '';
  child.stdout.on('data', (c) => {
    output += c.toString();
    process.stdout.write(c);
  });
  child.stderr.on('data', (c) => {
    output += c.toString();
    process.stdout.write(c);
  });
  const [exitCode] = await once(child, 'exit');
  const row = {
    name,
    loadCount,
    scope:
      loadCount === 0 && process.env.EDU_PYTEST_FULL === '1'
        ? '76-targeted'
        : 'two-historical-failures',
    load: '0 or 2 independent CPU math loops, each with a 240s hard duration',
    ms: Math.round(performance.now() - started),
    exitCode,
    loadExitedBeforeTestEnd: load.map((p) => p.exitCode !== null),
  };
  for (const process of load) if (process.exitCode === null) process.kill();
  runs.push(row);
  await writeFile(path.join(directory, name + '.txt'), output);
  await writeFile(path.join(directory, 'results.json'), JSON.stringify(runs, null, 2) + '\n');
  console.log(JSON.stringify(row));
}
if (runs.some((run) => run.exitCode !== 0)) process.exitCode = 1;
