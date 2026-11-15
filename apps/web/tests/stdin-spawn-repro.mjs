// Standard-library control only. This does not import or alter the EDU application.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const tests = fileURLToPath(new URL('.', import.meta.url));
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python);
const runs = [];
for (const mode of ['stdin', 'poll']) {
  for (let repeat = 1; repeat <= 3; repeat++) {
    const child = spawn(python, ['-u', path.join(tests, 'stdin-spawn-repro.py'), mode], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const run = { mode, repeat, events: [], stderr: '' };
    runs.push(run);
    let output = '';
    child.stdout.on('data', (chunk) => {
      output += chunk.toString();
      const lines = output.split('\n');
      output = lines.pop();
      for (const line of lines) if (line.trim()) run.events.push(JSON.parse(line));
    });
    child.stderr.on('data', (chunk) => {
      run.stderr += chunk.toString();
    });
    const release = setTimeout(() => {
      run.releaseAfterMs = 3000;
      if (!child.stdin.destroyed) child.stdin.end('stop\n');
    }, 3000);
    const cap = setTimeout(() => {
      run.forcedStop = true;
      child.kill();
    }, 20000);
    [run.exitCode] = await once(child, 'exit');
    clearTimeout(release);
    clearTimeout(cap);
    const begin = run.events.find((e) => e.stage === 'process_start_begin');
    const end = run.events.find((e) => e.stage === 'process_start_end');
    run.startMs = begin && end ? Math.round((end.time - begin.time) * 1000) : null;
    console.log(JSON.stringify(run));
  }
}
const directory = path.resolve(tests, '../artifacts/504-diagnosis');
await mkdir(directory, { recursive: true });
await writeFile(
  path.join(directory, 'stdlib-reproduction.json'),
  JSON.stringify(runs, null, 2) + '\n',
);
assert.ok(
  runs.every(
    (r) => r.exitCode === 0 && !r.forcedStop && r.events.some((e) => e.stage === 'finished'),
  ),
);
