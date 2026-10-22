const { spawnSync } = require('node:child_process');
const path = require('node:path');
if (process.platform !== 'win32') throw Error('Startup tests require Windows.');
const ps = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
// PowerShell 7's inherited module path can shadow Windows PowerShell modules.
const env = { ...process.env, PSModulePath: path.join(path.dirname(ps), 'Modules') };
for (const [exe, args] of [
  [ps, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(__dirname, 'verify-startup-protection.ps1')]],
  [process.execPath, [path.join(__dirname, 'verify-instance-lock.mjs')]],
  [ps, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(__dirname, 'verify-native-entry.ps1')]],
  [process.execPath, [path.resolve(__dirname, '../../../tools/verify-wallpaper-controls.cjs')]],
  [process.execPath, [path.join(__dirname, 'verify-emergency.cjs')]],
]) {
  const result = spawnSync(exe, args, { env, stdio: 'inherit', windowsHide: true, timeout: 90000 });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
console.log('Core safety suite passed. No installed app or startup settings changed.');
