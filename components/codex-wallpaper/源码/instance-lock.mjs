import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

// Named pipes are released by Windows even after a crash. A stale PID is not a lock.
export async function acquireInstance(dir) {
  const key = createHash('sha256').update(path.resolve(dir).toLowerCase()).digest('hex').slice(0, 32);
  const address = process.platform === 'win32' ? `\\\\.\\pipe\\codex-sunfaded-${key}` : path.join(dir, '.theme-instance.sock');
  const server = net.createServer(socket => socket.end());
  const acquired = await new Promise((resolve, reject) => {
    server.once('error', error => error.code === 'EADDRINUSE' ? resolve(false) : reject(error));
    server.listen(address, () => resolve(true));
  });
  if (!acquired) return null;
  const release = () => new Promise(resolve => server.close(resolve));
  try {
    // Upgrade compatibility: an old companion still uses a PID file. Verify its
    // command line; never mistake an unrelated process reusing its PID for it.
    const lock = path.join(dir, '.theme-runtime.lock');
    const pid = fs.existsSync(lock) ? Number(fs.readFileSync(lock, 'utf8')) : 0;
    if (process.platform === 'win32' && Number.isSafeInteger(pid) && pid > 0 && pid !== process.pid) {
      const ps = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
      const check = spawnSync(ps, ['-NoProfile', '-NonInteractive', '-Command',
        `$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $p=Get-CimInstance Win32_Process -Filter 'ProcessId=${pid}' -ErrorAction Stop; if($p){$p | Select-Object Name,CommandLine | ConvertTo-Json -Compress}`],
      { encoding: 'utf8', timeout: 5000, windowsHide: true,
        env: { ...process.env, PSModulePath: path.join(path.dirname(ps), 'Modules') } });
      if (check.error || check.status !== 0) throw Error('Cannot verify legacy companion identity');
      if (check.stdout.trim()) {
        const owner = JSON.parse(check.stdout.replace(/^\uFEFF/, ''));
        const escaped = path.join(dir, 'theme-runtime.mjs').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        if (owner.Name?.toLowerCase() === 'node.exe' && new RegExp(`(?:^|\\s)"?${escaped}"?(?:\\s|$)`, 'i').test(owner.CommandLine || '')) {
          await release();
          return null;
        }
      }
    }
    return release;
  } catch (error) { await release(); throw error; }
}
