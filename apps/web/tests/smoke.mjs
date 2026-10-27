import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const reservation = createServer();
reservation.listen(0, '127.0.0.1');
await once(reservation, 'listening');
const address = reservation.address();
assert.ok(address && typeof address !== 'string');
const port = address.port;
await new Promise((resolve, reject) =>
  reservation.close((error) => (error ? reject(error) : resolve())),
);

const server = spawn(
  process.execPath,
  ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)],
  {
    cwd: webRoot,
    env: { ...process.env, NEXT_TELEMETRY_DISABLED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
);
let output = '';
for (const stream of [server.stdout, server.stderr]) {
  stream.on('data', (chunk) => {
    output = (output + chunk.toString()).slice(-8000);
  });
}

try {
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 60_000;
  let home;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`Next.js exited: ${output}`);
    try {
      home = await fetch(url, { signal: AbortSignal.timeout(1500) });
      break;
    } catch {
      await sleep(250);
    }
  }
  assert.ok(home, `Next.js did not become ready: ${output}`);
  assert.equal(home.status, 200);
  const html = await home.text();
  assert.match(html, /lang="zh-CN"/);
  assert.match(html, /未然 EDU｜AI 时代的编程能力与学习成效诊断平台。/);
  assert.match(html, /学情诊断/);
  assert.match(html, /连接账户，查看测评/);

  for (const [route, title] of [
    ['explorer', '画像探索'],
    ['data', '学情诊断'],
    ['reports', '分析报告'],
  ]) {
    const response = await fetch(`${url}/${route}`, { signal: AbortSignal.timeout(5000) });
    assert.equal(response.status, 200);
    assert.match(await response.text(), new RegExp(title));
  }

  const sample = await fetch(`${url}/templates/${encodeURIComponent('评价长表模板.xlsx')}`, {
    signal: AbortSignal.timeout(5000),
  });
  assert.equal(sample.status, 200);
  const signature = new Uint8Array(await sample.arrayBuffer());
  assert.deepEqual([...signature.slice(0, 4)], [0x50, 0x4b, 0x03, 0x04]);

  const missing = await fetch(`${url}/missing-bootstrap-route`, {
    signal: AbortSignal.timeout(5000),
  });
  assert.equal(missing.status, 404);
  assert.match(await missing.text(), /页面不存在/);
  console.log(
    'PASS: production server starts; all workspace routes render; synthetic template downloads; missing route returns 404.',
  );
} finally {
  if (server.exitCode === null && server.signalCode === null) {
    const exited = once(server, 'exit');
    server.kill();
    await exited;
  }
}
