const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),fixture=fs.mkdtempSync(path.join(os.tmpdir(),'WallpaperControl-')),profile=path.join(fixture,'profile'),local=path.join(fixture,'local'),physical=path.join(fixture,'real installed theme'),capture=path.join(fixture,'capture.json');
fs.mkdirSync(path.join(profile,'.codex-plugins'),{recursive:true});fs.mkdirSync(physical,{recursive:true});
const anchor=path.join(profile,'.codex-plugins/start.vbs'),ps=path.join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe');
const env={...process.env,USERPROFILE:profile,LOCALAPPDATA:local,CODEX_CONTROL_CAPTURE:capture,PSModulePath:path.join(path.dirname(ps),'Modules')};
const literal=s=>"'"+s.replaceAll("'","''")+"'";
fs.writeFileSync(anchor,Buffer.concat([Buffer.from([255,254]),Buffer.from('folder = "'+physical+'"\r\n','utf16le')]));
for(const name of ['Set-ThemeEnabled.ps1','Restore-Startup.ps1','Emergency-Start.ps1','Fixed-Launcher.ps1'])fs.writeFileSync(path.join(physical,name),'param([switch]$Enable,[switch]$ArmRecovery)\n@{script='+literal(name)+';enable=[bool]$Enable;armRecovery=[bool]$ArmRecovery}|ConvertTo-Json|Set-Content -LiteralPath $env:CODEX_CONTROL_CAPTURE -Encoding UTF8');
const batches=[['关闭自动背景.cmd','disable','Set-ThemeEnabled.ps1'],['开启自动背景.cmd','enable','Set-ThemeEnabled.ps1'],['恢复原启动方式.cmd','restore','Restore-Startup.ps1'],['应急原生启动.cmd','native','Emergency-Start.ps1'],['修复背景.cmd','recover','Fixed-Launcher.ps1']];
for(const [batch,action,script]of batches){
 const text=fs.readFileSync(path.join(root,batch),'utf8');assert(text.includes('tools\\control-wallpaper.ps1'));assert(text.includes('-Action '+action));assert(!/(?<!\r)\n/.test(text));
 execFileSync(process.env.ComSpec,['/d','/c','call "'+path.join(root,batch)+'"'],{env,windowsHide:true,windowsVerbatimArguments:true,timeout:15000});
 const result=JSON.parse(fs.readFileSync(capture,'utf8').replace(/^\uFEFF/,''));assert.equal(result.script,script);assert.equal(result.enable,action==='enable');assert.equal(result.armRecovery,action==='recover');
}
fs.unlinkSync(anchor);fs.mkdirSync(path.join(local,'CodexSunFaded'),{recursive:true});
const plan=()=>JSON.parse(execFileSync(ps,['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(__dirname,'control-wallpaper.ps1'),'-Action','native','-Plan'],{env,windowsHide:true,encoding:'utf8'}).replace(/^\uFEFF/,''));
assert.equal(plan().folder,path.join(local,'CodexSunFaded'));
fs.writeFileSync(anchor,'folder = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\\CodexSunFaded")');assert.equal(plan().folder,path.join(local,'CodexSunFaded'));
console.log('Passed 7 wallpaper control checks: five real batch entries use the physical path; absent/legacy guards preserve the old path. No real client or config touched.');
