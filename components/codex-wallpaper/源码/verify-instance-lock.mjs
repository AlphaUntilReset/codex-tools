import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { acquireInstance } from './instance-lock.mjs';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'CodexLockTest-中文-'));
const moduleUrl = new URL('./instance-lock.mjs', import.meta.url).href;
// A living unrelated PID must not prevent the new companion from starting.
const sleeper = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore', windowsHide: true });
let child;
try {
  fs.writeFileSync(path.join(dir, '.theme-runtime.lock'), String(sleeper.pid));
  let release = await acquireInstance(dir);
  assert.ok(release, 'stale PID belonging to another process blocked startup');
  assert.equal(await acquireInstance(dir), null, 'duplicate owner acquired pipe');
  await release();
  const legacyScript = path.join(dir, 'theme-runtime.mjs');
  fs.writeFileSync(legacyScript, "console.log('ready');setInterval(()=>{},1000);");
  child = spawn(process.execPath, [legacyScript], { stdio: ['ignore', 'pipe', 'inherit'], windowsHide: true });
  await once(child.stdout, 'data');
  fs.writeFileSync(path.join(dir, '.theme-runtime.lock'), String(child.pid));
  assert.equal(await acquireInstance(dir), null, 'upgrade duplicated a live legacy companion');
  let ended = once(child, 'exit'); child.kill(); await ended;
  release = await acquireInstance(dir);
  assert.ok(release, 'dead legacy PID blocked startup'); await release();
  fs.writeFileSync(path.join(dir, '.theme-runtime.lock'), String(sleeper.pid));
  const script = path.join(dir, 'holder.mjs');
  fs.writeFileSync(script, `import {acquireInstance} from ${JSON.stringify(moduleUrl)}; await acquireInstance(${JSON.stringify(dir)}); console.log('ready');`);
  child = spawn(process.execPath, [script], { stdio: ['ignore', 'pipe', 'inherit'], windowsHide: true });
  await Promise.race([once(child.stdout, 'data'), new Promise((_, reject) => { const timer = setTimeout(()=>reject(Error('lock holder timeout')),10000); timer.unref(); })]);
  assert.equal(await acquireInstance(dir), null, 'second process acquired occupied pipe');
  ended = once(child, 'exit'); child.kill(); await ended;
  release = await acquireInstance(dir);
  assert.ok(release, 'crash left stale OS lock'); await release();
  console.log('Instance lock: unrelated living PID, live/dead legacy owner, Chinese path, duplicate process, crash recovery passed.');
} finally {
  if (child && child.exitCode === null) child.kill();
  sleeper.kill();
}
