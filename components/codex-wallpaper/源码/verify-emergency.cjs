const {chromium}=require('./test-browser.cjs');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'CodexEmergencyTest-'));
const quote=s=>"'"+s.replaceAll("'","''")+"'";
const ps=path.join(process.env.SystemRoot||'C:\\Windows','System32/WindowsPowerShell/v1.0/powershell.exe');
const checks=[];
fs.mkdirSync(path.join(__dirname,'..','记录'),{recursive:true});
function assert(ok,message){if(!ok)throw Error(message);}
function runVbs(source,entry,exists,code,login,watch=null,integrate=true,preference=null){
 const local=path.join(fixture,'vbs-'+checks.length),theme=path.join(local,'CodexSunFaded');
 fs.mkdirSync(theme,{recursive:true});
 const pet=path.join(local,'Programs','CodexToolsPet','CodexToolsPet.exe');fs.mkdirSync(path.dirname(pet),{recursive:true});fs.writeFileSync(pet,'mock');
 if(exists)fs.writeFileSync(path.join(theme,entry),'mock');
 if(integrate === 'guard') {
  const guard=path.join(local,'CodexNativeGuard');fs.mkdirSync(guard,{recursive:true});fs.writeFileSync(path.join(guard,'.pet-integration'),'enabled');
 } else if(integrate) fs.writeFileSync(path.join(theme,'.pet-integration'),'enabled');
 const vquote=s=>'"'+s.replaceAll('"','""')+'"';
 const log=path.join(local,'calls.txt');
 let text=fs.readFileSync(path.join(__dirname,source),'utf8').replace('Set shell = CreateObject("WScript.Shell")','Set shell = New MockShell');
 text=text.replace('Option Explicit','Option Explicit\nDim mockCode\nmockCode = '+code);
 text=text.replaceAll('fso.GetSpecialFolder(2)',vquote(local));
 const watchFile=path.join(local,'CodexGuard-test.done');
 if(watch==='complete')fs.writeFileSync(watchFile,'done');
 if(watch)text=text.replace('1 To 40','1 To 2').replace('WScript.Sleep 500','WScript.Sleep 10');
 if(source==='launch.vbs') text=text.replace('folder = fso.GetParentFolderName(WScript.ScriptFullName)','folder = '+vquote(theme));
 text+=`\nClass MockShell
 Public Function ExpandEnvironmentStrings(value)
  ExpandEnvironmentStrings = Replace(value, "%LOCALAPPDATA%", ${vquote(local)})
 End Function
 Public Function RegRead(key)
  ${preference===null?'Err.Raise 2':`RegRead = ${typeof preference==='number'?`CLng(${preference})`:vquote(preference)}`}
 End Function
 Public Function Run(command, style, wait)
  Dim out
  Set out = CreateObject("Scripting.FileSystemObject").OpenTextFile(${vquote(log)},8,True)
  out.WriteLine command
  out.Close
  If InStr(command,"powershell.exe") > 0 Or InStr(command,"cscript.exe") > 0 Then
   Run = mockCode
  Else
   Run = 0
  End If
 End Function
End Class\n`;
 const testScript=path.join(local,'test.vbs');fs.writeFileSync(testScript,text);
 const args=watch?['watch',watchFile,...(login?['login']:[])]:(login?['login']:[]);
 const result=spawnSync('cscript.exe',['//B','//Nologo',testScript,...args],{encoding:'utf8',timeout:10000});
 assert(result.status===0,'VBS compilation/execution failed: '+source);
 const calls=fs.existsSync(log)?fs.readFileSync(log,'utf8'):'';
 const fallback=watch?watch==='timeout':!exists||code!==0;
 assert(calls.includes('explorer.exe shell:AppsFolder')===fallback,'VBS native fallback mismatch');
 assert(calls.includes('CodexToolsPet.exe')===Boolean(fallback&&login&&integrate&&preference!==0),'VBS pet fallback mismatch: '+JSON.stringify({source,login,watch,integrate,preference}));
 if(watch==='complete')assert(!fs.existsSync(watchFile),'Watchdog acknowledgement not cleaned');
 checks.push({test:source,exists,code,login,watch,preference,nativeFallback:fallback,passed:true});
}
(async()=>{
 for(const [source,entry] of [['launch.vbs','Fixed-Launcher.ps1'],['Native-Guard.vbs','launch.vbs']]){
  runVbs(source,entry,true,0,false);runVbs(source,entry,false,0,true);runVbs(source,entry,true,1,true);runVbs(source,entry,false,0,true,null,false);
  for(const preference of [0,1,'0',2])runVbs(source,entry,false,0,true,null,true,preference);
 }
 runVbs('Native-Guard.vbs','launch.vbs',true,0,false,'timeout');
 runVbs('Native-Guard.vbs','launch.vbs',true,0,false,'complete');
 runVbs('Native-Guard.vbs','launch.vbs',false,0,true,null,'guard');
 runVbs('Native-Guard.vbs','launch.vbs',true,0,true,'timeout','guard');
 runVbs('Native-Guard.vbs','launch.vbs',true,0,true,'timeout',false);
 runVbs('Native-Guard.vbs','launch.vbs',true,0,true,'complete','guard');
 runVbs('Native-Guard.vbs','launch.vbs',true,0,true,'timeout','guard',0);
 const runtime=path.join(fixture,'theme-runtime.mjs');fs.copyFileSync(path.join(__dirname,'theme-runtime.mjs'),runtime);
 fs.writeFileSync(path.join(fixture,'config.json'),'{broken');
 let run=spawnSync(process.execPath,[runtime,'--stop'],{encoding:'utf8',timeout:5000});
 assert(run.status===0&&fs.existsSync(path.join(fixture,'.theme-runtime.stop')),'--stop depended on damaged resources');
 fs.writeFileSync(path.join(fixture,'.theme-disabled'),'disabled');
 run=spawnSync(process.execPath,[runtime],{encoding:'utf8',timeout:5000});
 assert(run.status===0,'Emergency disable depended on damaged resources');
 checks.push({test:'runtime_stop_and_disable_without_config_or_css',passed:true});
 // Real .NET CDP cleanup in isolated Chrome, with app/process discovery mocked.
 const browser=await chromium.launch({headless:true,args:['--remote-debugging-port=19328','--remote-debugging-address=127.0.0.1']});
 try{
  const page=await browser.newPage();
  await page.setContent('<html><head><style id="codex-sunfaded-local-theme">body{background:pink}</style></head><body><svg id="codex-sidebar-palette-filter"></svg><div id="unrelated">keep</div></body></html>');
  // Target filtering remains app://; substitute the own fixture page for discovery.
  const targets=await(await fetch('http://127.0.0.1:19328/json/list')).json();
  const own=targets.find(t=>t.type==='page');assert(own,'Chrome fixture missing');
  const emergency=path.join(fixture,'Emergency-Start.ps1');
  fs.writeFileSync(path.join(fixture,'AppxManifest.xml'),'<Package><Applications><Application Id="App" Executable="app/codex.exe" /></Applications></Package>');
  // Recovery is best-effort in production. In this isolated fixture, surface
  // every caught exception so a host/runtime failure cannot look like success.
  // Number each silent target skip the same way: a skipped cleanup fails here,
  // and the printed guard index says which skip fired.
  const emergencySource=fs.readFileSync(path.join(__dirname,'Emergency-Start.ps1'),'utf8').replace(/^\ufeff/,'');
  let skipTrace=0;
  const guardProbe=emergencySource.includes('if (($taskOwner.ExecutablePath -ne $taskExe')
    ? emergencySource.replace(/if \(\(\$taskOwner\.ExecutablePath -ne \$taskExe/,()=>"Write-Output ('G1:'+($taskOwner.ExecutablePath -ne $taskExe)+':'+($taskOwner.ExecutablePath -notmatch $taskPackagePattern)+':'+($taskOwner.CommandLine -match '(?:^|\\s)--type(?:=|\\s)')+' exe='+$taskExe+' owner='+$taskOwner.ExecutablePath);\n        if (($taskOwner.ExecutablePath -ne $taskExe")
    : emergencySource;
  if(guardProbe===emergencySource)throw Error('Guard probe anchor missing');
  fs.writeFileSync(emergency,'\ufeff'+guardProbe
    .replace(/catch\s*\{\s*\}/g,'catch { throw }')
    .replace(/\{ continue \}/g,()=>`{ Write-Output 'SKIP-${++skipTrace}'; continue }`));
  const wrapper=path.join(fixture,'verify.ps1');
  fs.writeFileSync(wrapper,'\ufeff'+`$ErrorActionPreference='Stop'
function Get-AppxPackage {param($Name,$ErrorAction);[pscustomobject]@{InstallLocation=${quote(fixture)};PackageFamilyName='OpenAI.Codex_2p2nqsd0c76g0'}}
function Get-NetTCPConnection {param($LocalPort,$State,$ErrorAction);[pscustomobject]@{OwningProcess=777;LocalAddress='127.0.0.1'}}
function Get-CimInstance {param($ClassName,$Filter,$ErrorAction);if($Filter -match "(?i)name='codex.exe'"){[pscustomobject]@{ProcessId=777;CommandLine='codex.exe --remote-debugging-port=19328';ExecutablePath=${quote(path.join(fixture,'app','codex.exe'))}}}}
function Invoke-RestMethod {param($Uri,$TimeoutSec,$ErrorAction);if($Uri -ne 'http://127.0.0.1:19328/json/list'){throw 'Wrong cleanup port'};ConvertFrom-Json ${quote(JSON.stringify([{...own,url:'app://fixture'}]))}}
& ${quote(emergency)} -NoLaunch
`);
  const wrongOwner=path.join(fixture,'wrong-owner.ps1');
  fs.writeFileSync(wrongOwner,fs.readFileSync(wrapper,'utf8').replace('OwningProcess=777','OwningProcess=778'));
  const negative=spawnSync(ps,['-NoProfile','-ExecutionPolicy','Bypass','-File',wrongOwner],{encoding:'utf8',timeout:10000});
  assert(negative.status===0,'Emergency negative fixture execution failed: '+(negative.error?.message||'')+negative.stdout+negative.stderr);
  assert(await page.locator('#codex-sunfaded-local-theme').count()===1,'Cleanup touched port owned by an unrelated process');
  checks.push({test:'reject_unrelated_listener_owner',passed:true});
  const result=spawnSync(ps,['-NoProfile','-ExecutionPolicy','Bypass','-File',wrapper],{encoding:'utf8',timeout:10000});
  assert(result.status===0,'Emergency fixture execution failed: '+(result.error?.message||'')+result.stdout+result.stderr);
  const state=await page.evaluate(()=>({theme:!!document.getElementById('codex-sunfaded-local-theme'),palette:!!document.getElementById('codex-sidebar-palette-filter'),unrelated:!!document.getElementById('unrelated')}));
  if(state.theme||state.palette||!state.unrelated){
   const after=await(await fetch('http://127.0.0.1:19328/json/list')).json();
   assert(false,'.NET cleanup failed or affected unrelated content: '+JSON.stringify({state,targets:after.map(t=>({type:t.type,url:t.url,ws:t.webSocketDebuggerUrl})),fixtureStdout:result.stdout,fixtureStderr:result.stderr}));
  }
  checks.push({test:'real_dotnet_cleanup_renamed_manifest_executable_isolated_chrome',passed:true,unrelatedPreserved:true});
 }finally{await browser.close();}
 fs.writeFileSync(path.join(__dirname,'..','记录','emergency-verification.json'),JSON.stringify({fixture,realClientUntouched:true,checks},null,2));
 console.log('Passed '+checks.length+' VBS/runtime/emergency checks; real client unchanged.');
})().catch(e=>{console.error(e);process.exitCode=1;});
