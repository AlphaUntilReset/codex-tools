$ErrorActionPreference='Stop'
# Load the built-in hashing function before mocking registry/COM commands.
$null=Get-Command Get-FileHash
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('CodexThemeProtection-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$global:protectionTesttestRoot=$testRoot
$global:protectionTestevents=@()
$global:protectionTestscenario=''
$global:protectionTestrunning=$false
$global:protectionTestregistry=@{}
$global:protectionTeststopped=@()
$global:protectionTestshortcutSaved=$false
$global:protectionTestshortcutFailure=$false
Add-Type -TypeDefinition @'
namespace SunFaded.Native {
 public static class NativeShortcut {
  public static bool Fail=false;
  public static string PhysicalFile(string path){return path;}
  public static string LoginCommand(string id){return "cmd.exe /d /c cscript.exe //B start.vbs login || explorer.exe shell:AppsFolder\\"+id;}
  public static void Create(string file,string id,string args){if(Fail)throw new System.Exception("Mock desktop write denied");System.IO.File.WriteAllText(file,"native-link-fixture");}
 }
 public static class PackageActivation {
  public static string Mode="";
  public static System.Collections.Generic.List<string> Calls=new System.Collections.Generic.List<string>();
  public static bool Running=false;
  public static uint Activate(string id,string args) {
   Calls.Add(args);
   if(Mode=="api_failure" || (Mode=="arguments_failure" && args!="")) throw new System.Exception("Mock activation failed");
   Running=Mode!="early_exit";
   return 9090;
  }
  public static bool Validate() {return true;}
  public static string PackageIdentity(uint pid) {return Mode=="packaged_companion"?"unexpected.package":null;}
 }
}
'@
# All app/process/registry effects below are mocked; only fixture files are real.
function Start-Process {
 param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$ErrorAction)
 $global:protectionTestevents+=@{file=$FilePath;args=$ArgumentList}
 if ($FilePath -like '*fake-node.exe' -and $global:protectionTestscenario -eq 'helper_failure') {throw 'Mock Node launch failure'}
}
function Start-Sleep { param($Milliseconds,$Seconds) }
function New-CimInstance {param($ClassName,[switch]$ClientOnly,$Property);return [pscustomobject]$Property}
function Invoke-CimMethod {
 param($ClassName,$MethodName,$Arguments)
 $global:protectionTestevents+=@{wmi=$true;command=$Arguments.CommandLine;startup=$Arguments.ProcessStartupInformation}
 if($global:protectionTestscenario -eq 'helper_failure' -and $Arguments.CommandLine -like '*fake-node.exe*'){return [pscustomobject]@{ReturnValue=21;ProcessId=0}}
 return [pscustomobject]@{ReturnValue=0;ProcessId=9010}
}
function Get-AppxPackage { param($Name,$ErrorAction) $global:protectionTestpackageReads++;if($global:protectionTestscenario -eq 'package_failure'){throw 'Mock package discovery failure'};if($global:protectionTestscenario -eq 'transient_registration' -and $global:protectionTestpackageReads -eq 1){return}; [pscustomobject]@{InstallLocation=$global:protectionTesttestRoot;PackageFamilyName='OpenAI.Codex_2p2nqsd0c76g0'} }
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) if($global:protectionTestscenario -eq 'occupied_port'){[pscustomobject]@{OwningProcess=777}};if($global:protectionTestscenario -eq 'already_running_other_version_with_port'){[pscustomobject]@{OwningProcess=9090}} }
function Get-CimInstance {
 param($ClassName,$Filter,$ErrorAction)
 if ($Filter -match "(?i)name='ChatGPT.exe'") {
  if($global:protectionTestscenario -like 'already_running_other_version*'){[pscustomobject]@{Name='ChatGPT.exe';ExecutablePath='C:\Program Files\WindowsApps\OpenAI.Codex_9.0.0.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe';CommandLine='ChatGPT.exe';ProcessId=9090};return}
  if($global:protectionTestscenario -eq 'already_running_old_and_new'){
   [pscustomobject]@{Name='ChatGPT.exe';ExecutablePath='C:\Program Files\WindowsApps\OpenAI.Codex_9.0.0.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe';CommandLine='ChatGPT.exe';ProcessId=9089}
  }
  if($global:protectionTestscenario -eq 'unrelated_cli'){
   [pscustomobject]@{Name='codex.exe';ExecutablePath=(Join-Path $global:protectionTesttestRoot 'OpenAI\Codex\bin\build\codex.exe');CommandLine='codex.exe app-server';ProcessId=8001}
   [pscustomobject]@{Name='codex.exe';ExecutablePath='C:\Program Files\WindowsApps\Other.Codex_9.0.0.0_x64__2p2nqsd0c76g0\app\codex.exe';CommandLine='codex.exe';ProcessId=8002}
   [pscustomobject]@{Name='codex.exe';ExecutablePath=(Join-Path $global:protectionTesttestRoot $global:protectionTestentry);CommandLine='codex.exe --type=renderer';ProcessId=8003}
  }
  if($global:protectionTestrunning -or [SunFaded.Native.PackageActivation]::Running){[pscustomobject]@{Name=[IO.Path]::GetFileName($global:protectionTestentry);ExecutablePath=(Join-Path $global:protectionTesttestRoot $global:protectionTestentry);CommandLine=$global:protectionTestentry;ProcessId=9090}}
 } elseif ($Filter -eq "name='node.exe'") {
  [pscustomobject]@{Name='node.exe';ProcessId=101;CommandLine=('"node.exe" "'+(Join-Path $global:protectionTesttestRoot 'theme-runtime.mjs')+'"')}
  [pscustomobject]@{Name='node.exe';ProcessId=202;CommandLine='node.exe unrelated.mjs'}
 } elseif ($Filter -eq 'ProcessId=101') {if($global:protectionTestscenario -eq 'graceful_exit'){return};[pscustomobject]@{Name='node.exe';ProcessId=101;CommandLine=('"node.exe" "'+(Join-Path $global:protectionTesttestRoot 'theme-runtime.mjs')+'"')}}
 elseif ($Filter -eq 'ProcessId=777') {[pscustomobject]@{ExecutablePath='C:\other.exe'}}
}
function Stop-Process {param($Id,[switch]$Force,$ErrorAction);$global:protectionTeststopped+=$Id}
function Wait-Process {param($Id,$Timeout,$ErrorAction)}
# Model destructive New-Item -Force semantics, so regressions erase sentinels.
function New-ItemProperty {param($LiteralPath,$Name,$Value,$PropertyType,[switch]$Force);if(!$global:protectionTestregistry.ContainsKey($LiteralPath)){throw 'Missing registry key'};$global:protectionTestregistry[$LiteralPath][$Name]=$Value;$global:protectionTestevents+=@{registryWrite=$Name;value=$Value}}
function Remove-ItemProperty {param($LiteralPath,$Name,$ErrorAction);if($global:protectionTestregistry.ContainsKey($LiteralPath)){$global:protectionTestregistry[$LiteralPath].Remove($Name)}}
function Get-ItemProperty {param($LiteralPath,$Name,$ErrorAction);if($global:protectionTestregistry.ContainsKey($LiteralPath)){[pscustomobject]$global:protectionTestregistry[$LiteralPath]}}
function Test-Path {param($LiteralPath,$Path,$PathType,$ErrorAction);$p=if($LiteralPath){$LiteralPath}else{$Path};if($p -like 'HKCU:*'){return $global:protectionTestregistry.ContainsKey($p)};Microsoft.PowerShell.Management\Test-Path @PSBoundParameters}
function New-Item {param($Path,$ItemType,[switch]$Force);if($Path -like 'HKCU:*'){if($Force -or !$global:protectionTestregistry.ContainsKey($Path)){$global:protectionTestregistry[$Path]=@{}}}else{Microsoft.PowerShell.Management\New-Item @PSBoundParameters}}
function New-Object {
 param($ComObject)
 if($ComObject -ne 'WScript.Shell'){throw 'Unexpected COM use in fixture'}
 $mockShell=[pscustomobject]@{}
 $mockShell | Add-Member ScriptMethod CreateShortcut {
  param($path)
  $shortcut=[pscustomobject]@{TargetPath='';Arguments='';WorkingDirectory='';Description=''}
  $shortcut | Add-Member ScriptMethod Save {if($global:protectionTestshortcutFailure){throw 'Mock desktop write denied'};$global:protectionTestshortcutSaved=$true;$global:protectionTestsavedShortcutArgs=$this.Arguments}
  return $shortcut
 }
 return $mockShell
}
function Assert($condition,$message){if(!$condition){throw $message}}
$results=@()
foreach($name in @('Fixed-Launcher.ps1','Emergency-Start.ps1','Restore-Startup.ps1','Set-ThemeEnabled.ps1')){Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $testRoot}
[IO.File]::WriteAllText((Join-Path $testRoot 'fake-node.exe'),'fixture')
[IO.File]::WriteAllText((Join-Path $testRoot 'fake-pet.exe'),'fixture')
$config=@{enabled=$true;port=9228;node=(Join-Path $testRoot 'fake-node.exe');pet=(Join-Path $testRoot 'fake-pet.exe');launchPetAtLogin=$true}
foreach($case in @('normal','renamed_main','transient_registration','unrelated_cli','corrupt_config','missing_config','invalid_port','missing_node','missing_runtime','arguments_failure','api_failure','early_exit','package_failure','helper_failure','packaged_companion','occupied_port','disabled_marker','already_running','already_running_other_version','already_running_other_version_with_port','already_running_old_and_new','already_running_renamed_main','already_running_desktop','already_running_desktop_api_failure')){
 $global:protectionTestscenario=$case;$global:protectionTestevents=@();$global:protectionTestrunning=($case -like 'already_running*');$global:protectionTeststopped=@()
 $global:protectionTestpackageReads=0
 $global:protectionTestentry=if($case -in @('renamed_main','already_running_old_and_new','already_running_renamed_main','unrelated_cli')){'app\codex.exe'}else{'app\ChatGPT.exe'}
 [IO.File]::WriteAllText((Join-Path $testRoot 'AppxManifest.xml'),('<Package><Applications><Application Id="App" Executable="'+$global:protectionTestentry+'" /></Applications></Package>'))
 [SunFaded.Native.PackageActivation]::Calls.Clear();[SunFaded.Native.PackageActivation]::Running=$false;[SunFaded.Native.PackageActivation]::Mode=if($case -eq 'already_running_desktop_api_failure'){'api_failure'}else{$case}
 [IO.File]::WriteAllText((Join-Path $testRoot 'config.json'),($config|ConvertTo-Json))
 [IO.File]::WriteAllText((Join-Path $testRoot 'SunFaded.css'),'body{}')
 [IO.File]::WriteAllText((Join-Path $testRoot 'theme-runtime.mjs'),'// fixture')
 [IO.File]::WriteAllText((Join-Path $testRoot 'instance-lock.mjs'),'// fixture')
 Remove-Item -LiteralPath (Join-Path $testRoot '.theme-disabled') -ErrorAction SilentlyContinue
 if($case -eq 'corrupt_config'){[IO.File]::WriteAllText((Join-Path $testRoot 'config.json'),'{broken')}
 if($case -eq 'missing_config'){Remove-Item -LiteralPath (Join-Path $testRoot 'config.json')}
 if($case -eq 'invalid_port'){[IO.File]::WriteAllText((Join-Path $testRoot 'config.json'),(($config+@{}) | ConvertTo-Json).Replace('9228','12'))}
 if($case -eq 'missing_node'){[IO.File]::WriteAllText((Join-Path $testRoot 'config.json'),($config|ConvertTo-Json).Replace('fake-node.exe','missing-node.exe'))}
 if($case -eq 'missing_runtime'){Remove-Item -LiteralPath (Join-Path $testRoot 'theme-runtime.mjs')}
 if($case -eq 'disabled_marker'){[IO.File]::WriteAllText((Join-Path $testRoot '.theme-disabled'),'disabled')}
 & (Join-Path $testRoot 'Fixed-Launcher.ps1') -Login:($case -notlike '*desktop*')
 $status=Get-Content -LiteralPath (Join-Path $testRoot 'launcher-status.json') -Raw | ConvertFrom-Json
 Assert ($status.state -eq 'launcher_completed') "$case aborted startup"
 Assert ($global:protectionTestrunning -or [SunFaded.Native.PackageActivation]::Calls.Count -gt 0) "$case did not attempt app activation"
 if($case -in @('api_failure','early_exit')){Assert (@($global:protectionTestevents|Where-Object {$_.file -eq 'explorer.exe'}).Count -eq 1) "$case missing native shell fallback"}
 if($case -eq 'arguments_failure'){Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 2 -and [SunFaded.Native.PackageActivation]::Calls[1] -eq '') 'Did not retry without arguments'}
 if($case -in @('corrupt_config','missing_config','invalid_port','missing_node','missing_runtime','disabled_marker')){Assert (!$status.themeEnabled) "$case enabled invalid theme"}
 if($case -eq 'already_running'){Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'Login unnecessarily activated an existing window'}
 if($case -like 'already_running_other_version*'){Assert ($status.alreadyRunning -and [SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'A running client version was missed after package registration changed'}
 if($case -in @('already_running_old_and_new','already_running_renamed_main')){Assert ($status.alreadyRunning -and [SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'A renamed main process was missed during an update'}
 if($case -in @('renamed_main','transient_registration','unrelated_cli')){Assert (!$status.alreadyRunning -and $status.activationMode -eq 'WindowsApplicationActivationManager' -and [SunFaded.Native.PackageActivation]::Calls.Count -eq 1) "$case was mistaken for an existing client or failed process confirmation"}
 if($case -eq 'transient_registration'){Assert ($global:protectionTestpackageReads -eq 2) 'Registration was not rediscovered after a transient update gap'}
 if($case -in @('normal','already_running','already_running_desktop','already_running_other_version_with_port')){
  Assert ($status.companion.launchMode -eq 'windows-wmi-unpackaged' -and !$status.companion.packageIdentity) 'Launcher did not detach the companion from the client package'
  $created=@($global:protectionTestevents|Where-Object {$_.wmi -and $_.command -like '*theme-runtime.mjs*'})
  Assert ($created.Count -eq 1 -and $created[0].startup.CreateFlags -eq 0x09000000) 'Launcher did not use independent WMI startup'
  Assert ($created[0].startup.EnvironmentVariables -contains 'SUNFADED_CDP_PORT=9228') 'Launcher lost the configured port'
  Assert (@($global:protectionTestevents|Where-Object {$_.file -like '*fake-node.exe' -or $_.file -like '*fake-pet.exe'}).Count -eq 0) 'Launcher retained an ordinary packaged child path'
 }
 if($case -in @('helper_failure','packaged_companion')){Assert (!$status.companion -and ($status.warnings -join ';') -like '*Companion unavailable*') 'Independent launch failure was reported as successful'}
 if($case -eq 'already_running_desktop'){Assert $status.restartRequired 'Missing debug port was silently ignored'}
 if($case -like 'already_running_desktop*'){
  Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 1 -and [SunFaded.Native.PackageActivation]::Calls[0] -eq '') 'Desktop did not activate existing window without debug arguments'
  Assert ($status.alreadyRunning -eq $true) 'Existing instance was treated as a new app launch'
  if($case -eq 'already_running_desktop_api_failure'){Assert (@($global:protectionTestevents|Where-Object {$_.file -eq 'explorer.exe'}).Count -eq 1) 'Existing window activation has no shell fallback'}
 }
 Assert ($global:protectionTeststopped.Count -eq 0) "$case killed a process"
 $results+=@{case=$case;passed=$true;mode=$status.activationMode}
}
# Explicit login intent applies to execution and inspection; inspection has no effects.
$preferenceKey='HKCU:\Software\CodexToolsPet'
foreach($preferenceCase in @(@{name='absent';allow=$true},@{name='disabled';value=[int]0;allow=$false},@{name='enabled';value=[int]1;allow=$true},@{name='invalid_string';value='0';allow=$true},@{name='invalid_dword';value=[int]2;allow=$true},@{name='invalid_qword';value=[long]0;allow=$true})){
 $global:protectionTestregistry.Remove($preferenceKey)
 if($preferenceCase.ContainsKey('value')){$global:protectionTestregistry[$preferenceKey]=@{LoginEnabled=$preferenceCase.value}}
 $global:protectionTestscenario='normal';$global:protectionTestevents=@();$global:protectionTestrunning=$false
 [SunFaded.Native.PackageActivation]::Running=$false;[SunFaded.Native.PackageActivation]::Calls.Clear();[SunFaded.Native.PackageActivation]::Mode='normal'
 $plan=& (Join-Path $testRoot 'Fixed-Launcher.ps1') -Login -Plan | ConvertFrom-Json
 Assert ($plan.LoginPet -eq $preferenceCase.allow) ('Plan ignored '+$preferenceCase.name+' pet login intent')
 Assert ($global:protectionTestevents.Count -eq 0 -and [SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'Plan caused process or registry activity'
 & (Join-Path $testRoot 'Fixed-Launcher.ps1') -Login
 $petStarts=@($global:protectionTestevents|Where-Object {$_.wmi -and $_.command -like '*fake-pet.exe*'})
 Assert (($petStarts.Count -eq 1) -eq $preferenceCase.allow) ('Launcher ignored '+$preferenceCase.name+' pet login intent')
 Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 1) 'Pet login intent affected client activation'
 Assert (@($global:protectionTestevents|Where-Object {$_.wmi -and $_.command -like '*theme-runtime.mjs*'}).Count -eq 1) 'Pet login intent affected background startup'
 $results+=@{case=('pet_login_intent_'+$preferenceCase.name);passed=$true}
}
$global:protectionTestregistry.Remove($preferenceKey)
# Repair waits for the user's exit; it never closes a real or fixture app itself.
$global:protectionTestscenario='normal';$global:protectionTestevents=@();$global:protectionTestrunning=$true
[SunFaded.Native.PackageActivation]::Mode='normal';[SunFaded.Native.PackageActivation]::Running=$false;[SunFaded.Native.PackageActivation]::Calls.Clear()
& (Join-Path $testRoot 'Fixed-Launcher.ps1') -CompanionOnly
Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'Companion-only launch activated the client'
$results+=@{case='companion_only_without_client_activation';passed=$true}
function Start-Sleep {
 param($Milliseconds,$Seconds)
 $before=Get-Content -LiteralPath (Join-Path $global:protectionTesttestRoot 'launcher-status.json') -Raw|ConvertFrom-Json
 Assert ($before.state -eq 'waiting_for_user_exit') 'Recovery did not announce its wait'
 Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'Recovery launched before the user exited'
 $global:protectionTestrunning=$false
}
$global:protectionTestpackageReads=0
& (Join-Path $testRoot 'Fixed-Launcher.ps1') -WaitForExit
Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 1 -and [SunFaded.Native.PackageActivation]::Calls[0] -like '*--remote-debugging-port=9228*') 'Recovery failed to reopen with the configured debug port'
Assert ($global:protectionTestpackageReads -ge 2) 'Recovery reused the pre-update package path after waiting'
Assert ($global:protectionTeststopped.Count -eq 0) 'Recovery stopped a process'
$results+=@{case='recover_after_user_exit';passed=$true}
function Start-Sleep {param($Milliseconds,$Seconds)}
$global:protectionTestrunning=$true;[SunFaded.Native.PackageActivation]::Running=$false;[SunFaded.Native.PackageActivation]::Calls.Clear()
function Start-Sleep {param($Milliseconds,$Seconds);[IO.File]::WriteAllText((Join-Path $global:protectionTesttestRoot '.theme-disabled'),'disabled')}
$cancelled=$false
try { & (Join-Path $testRoot 'Fixed-Launcher.ps1') -WaitForExit } catch { $cancelled=$_.Exception.Message -like '*cancelled*' }
Assert ($cancelled -and [SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'Disabled theme did not cancel waiting recovery'
Remove-Item -LiteralPath (Join-Path $testRoot '.theme-disabled')
$results+=@{case='recover_cancelled_when_disabled';passed=$true}
function Start-Sleep {param($Milliseconds,$Seconds)}
[SunFaded.Native.PackageActivation]::Calls.Clear();$global:protectionTestevents=@()
& (Join-Path $testRoot 'Fixed-Launcher.ps1') -ArmRecovery
Assert ([SunFaded.Native.PackageActivation]::Calls.Count -eq 0) 'Arming recovery activated the client'
$armed=@($global:protectionTestevents|Where-Object {$_.wmi -and $_.command -like '*powershell.exe* -WaitForExit*'})
Assert ($armed.Count -eq 1 -and $armed[0].startup.CreateFlags -eq 0x09000000) 'Recovery waiter was not detached'
$results+=@{case='arm_independent_recovery_waiter';passed=$true}
# Emergency does not parse config, read theme files, or need Node. No actual CDP access.
$global:protectionTestscenario='package_failure';$global:protectionTestevents=@();$global:protectionTeststopped=@()
[IO.File]::WriteAllText((Join-Path $testRoot 'config.json'),'{broken')
Remove-Item -LiteralPath (Join-Path $testRoot 'theme-runtime.mjs') -ErrorAction SilentlyContinue
& (Join-Path $testRoot 'Emergency-Start.ps1')
Assert ($global:protectionTeststopped.Count -eq 1 -and $global:protectionTeststopped[0] -eq 101) 'Emergency touched unrelated process or missed its own helper'
Assert (@($global:protectionTestevents|Where-Object {$_.file -eq 'explorer.exe'}).Count -eq 1) 'Emergency failed to dispatch native app'
Assert (Test-Path -LiteralPath (Join-Path $testRoot '.theme-disabled')) 'Emergency did not persist disable'
$results+=@{case='independent_emergency';passed=$true}
$global:protectionTestscenario='graceful_exit';$global:protectionTeststopped=@()
& (Join-Path $testRoot 'Emergency-Start.ps1') -NoLaunch
Assert ($global:protectionTeststopped.Count -eq 0) 'Emergency killed a helper that already exited gracefully'
$results+=@{case='graceful_emergency_exit';passed=$true}

# Restore uses mocked registry/shortcut APIs; no real settings touched.
$runKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$approvalKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$global:protectionTestregistry=@{};$global:protectionTestregistry[$runKey]=@{UnrelatedApp='keep-run'};$global:protectionTestregistry[$approvalKey]=@{UnrelatedApp='keep-approval';CodexToolsPet='keep-unmanaged-pet'}
$global:protectionTestshortcutSaved=$false;$global:protectionTestevents=@()
& (Join-Path $testRoot 'Restore-Startup.ps1') | Out-Null
Assert ($global:protectionTestregistry[$runKey].CodexSunFaded -like 'explorer.exe shell:AppsFolder*') 'Restore lost native login path'
Assert ($global:protectionTestshortcutSaved -and $global:protectionTestsavedShortcutArgs -like 'shell:AppsFolder*') 'Restore failed without backup/config'
Assert ($global:protectionTestregistry[$runKey].UnrelatedApp -eq 'keep-run') 'Restore erased another login entry'
Assert ($global:protectionTestregistry[$approvalKey].UnrelatedApp -eq 'keep-approval') 'Restore erased another startup approval'
Assert ($global:protectionTestregistry[$approvalKey].CodexToolsPet -eq 'keep-unmanaged-pet') 'Restore changed pet without backup'
$results+=@{case='restore_without_config_node_or_backup';passed=$true}
# A desktop failure must not skip pet rollback, for both original approval states.
New-Item -ItemType Directory -Path (Join-Path $testRoot 'backup') -Force | Out-Null
foreach($originalApproval in @('absent','disabled')) {
 $global:protectionTestregistry[$runKey]=@{UnrelatedApp='keep-run'}
 $global:protectionTestregistry[$approvalKey]=@{UnrelatedApp='keep-approval';CodexToolsPet=[byte[]](3,0,0,0)}
 $backupState=@{petRun='"'+(Join-Path $env:LOCALAPPDATA 'Programs\CodexToolsPet\CodexToolsPet.exe')+'" --launch-codex';approvedExisted=($originalApproval -eq 'disabled');approvedBytes=if($originalApproval -eq 'disabled'){[Convert]::ToBase64String([byte[]](3,0,0,0,1,2,3,4))}else{$null}}
 $backupState|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $testRoot 'backup\startup-original.json') -Encoding UTF8
 $global:protectionTestshortcutFailure=$true
 $restoreFailed=$false
 try { & (Join-Path $testRoot 'Restore-Startup.ps1') | Out-Null } catch { $restoreFailed=$true }
 finally {$global:protectionTestshortcutFailure=$false}
 Assert $restoreFailed 'Partial recovery silently reported success'
 $restoreStatus=Get-Content -LiteralPath (Join-Path $testRoot 'restore-status.json') -Raw | ConvertFrom-Json
 Assert ($restoreStatus.state -eq 'partial_failure' -and ($restoreStatus.errors -join ';') -like '*Desktop shortcut:*') 'Incomplete recovery has no actionable status'
 if($originalApproval -eq 'absent') {Assert (!$global:protectionTestregistry[$approvalKey].ContainsKey('CodexToolsPet')) 'Desktop error skipped original absent pet approval restoration'}
 else {Assert ([Convert]::ToBase64String($global:protectionTestregistry[$approvalKey].CodexToolsPet) -eq $backupState.approvedBytes) 'Desktop error skipped exact pet approval bytes restoration'}
 Assert ($global:protectionTestregistry[$runKey].CodexSunFaded -like 'explorer.exe shell:AppsFolder*') 'Desktop failure lost native client login'
 Assert ($global:protectionTestregistry[$runKey].UnrelatedApp -eq 'keep-run' -and $global:protectionTestregistry[$approvalKey].UnrelatedApp -eq 'keep-approval') 'Partial recovery changed unrelated startup state'
 $results+=@{case=('restore_desktop_failure_pet_'+$originalApproval);passed=$true}
}
# New installation, no desktop shortcut and no pet; optional integration separately.
$originalLocalAppData=$env:LOCALAPPDATA
$originalUserProfile=$env:USERPROFILE
try {
 foreach($installCase in @('standalone','integrated','integrated_desktop_failure','public_plan','public_assets','public_missing')) {
  $integrate=$installCase -like 'integrated*'
  $env:LOCALAPPDATA=Join-Path $testRoot ('local-'+$installCase)
  $env:USERPROFILE=Join-Path $testRoot ('user-'+$installCase)
  $installerSource=Join-Path $testRoot ('install-source-'+$installCase)
  New-Item -ItemType Directory -Path $installerSource -Force | Out-Null
  foreach($name in @('Fixed-Launcher.ps1','Emergency-Start.ps1','Restore-Startup.ps1','Set-ThemeEnabled.ps1','Native-Guard.vbs','Native-Shortcut.cs','launch.vbs','PackageActivation.cs','ATTRIBUTION.txt')) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $installerSource }
  foreach($name in @('SunFaded.css','Sidebar.css','SidebarPalette.svg','theme-runtime.mjs','instance-lock.mjs')) { [IO.File]::WriteAllText((Join-Path $installerSource $name),'fixture') }
  New-Item -ItemType Directory -Path (Join-Path $testRoot '图片') -Force | Out-Null
  [IO.File]::WriteAllText((Join-Path $testRoot '图片\SunFaded.png'),'fixture')
  $installerText=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'Install-FixedConfig.ps1')).Replace("([Environment]::GetFolderPath('Desktop'))",("'"+$installerSource.Replace("'","''")+"'"))
  [IO.File]::WriteAllText((Join-Path $installerSource 'Install-FixedConfig.ps1'),$installerText,[Text.UTF8Encoding]::new($true))
  $global:protectionTestregistry[$runKey]=@{UnrelatedApp='keep-run'}
  $global:protectionTestregistry[$approvalKey]=@{UnrelatedApp='keep-approval'}
  if($integrate) {
   New-Item -ItemType Directory -Path (Join-Path $env:LOCALAPPDATA 'CodexToolsPet') -Force | Out-Null
   [IO.File]::WriteAllText((Join-Path $env:LOCALAPPDATA 'CodexToolsPet\settings.json'),'{"autostart":true}')
   $global:protectionTestregistry[$runKey].CodexToolsPet='"'+(Join-Path $env:LOCALAPPDATA 'Programs\CodexToolsPet\CodexToolsPet.exe')+'" --launch-codex'
  }
  $global:protectionTestscenario='normal'
  $publicAssets=$null
  if($installCase -like 'public_*') {
   $publicAssets=Join-Path $testRoot ('own-images-'+$installCase)
   New-Item -ItemType Directory -Path $publicAssets -Force | Out-Null
   foreach($name in @('SunFaded.css','Sidebar.css','SidebarPalette.svg','SunFaded.png')) {
    [IO.File]::WriteAllText((Join-Path $publicAssets $name),('user-supplied-'+$name))
    if($name -ne 'SunFaded.png'){Remove-Item -LiteralPath (Join-Path $installerSource $name)}
   }
   Remove-Item -LiteralPath (Join-Path $testRoot '图片\SunFaded.png')
   if($installCase -eq 'public_plan') {
    $global:protectionTestevents=@()
    $planned=& (Join-Path $installerSource 'Install-FixedConfig.ps1') -AssetDirectory $publicAssets -Plan | ConvertFrom-Json
    Assert ($planned.planOnly -and $planned.assets -eq $publicAssets) 'Plan did not use the supplied asset directory'
    Assert (!(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'CodexSunFaded'))) 'Plan created an installed runtime'
    Assert (!$global:protectionTestregistry[$runKey].ContainsKey('CodexSunFaded') -and $global:protectionTestevents.Count -eq 0) 'Plan changed startup or launched a process'
    $results+=@{case='public_assets_plan_no_mutations';passed=$true}
    continue
   }
   if($installCase -eq 'public_missing') {
    Remove-Item -LiteralPath (Join-Path $publicAssets 'Sidebar.css')
    $missingFailed=$false
    try {& (Join-Path $installerSource 'Install-FixedConfig.ps1') -AssetDirectory $publicAssets | Out-Null} catch {$missingFailed=$_.Exception.Message -like '*prepare_images.py*'}
    Assert ($missingFailed -and !(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'CodexSunFaded'))) 'Missing user assets caused a partial installation'
    Assert (!$global:protectionTestregistry[$runKey].ContainsKey('CodexSunFaded')) 'Missing assets changed startup'
    $results+=@{case='public_missing_assets_no_mutations';passed=$true}
    continue
   }
  }
  if (!$integrate) {
   New-Item -ItemType Directory -Path (Join-Path $env:LOCALAPPDATA 'CodexNativeGuard') -Force | Out-Null
   [IO.File]::WriteAllText((Join-Path $env:LOCALAPPDATA 'CodexNativeGuard\.pet-integration'),'stale-marker')
  }
  if($installCase -eq 'integrated_desktop_failure') {
   $global:protectionTestshortcutFailure=$true
   [SunFaded.Native.NativeShortcut]::Fail=$true
   $installFailed=$false
   try {& (Join-Path $installerSource 'Install-FixedConfig.ps1') -IntegratePet | Out-Null} catch {$installFailed=$true}
   finally {$global:protectionTestshortcutFailure=$false;[SunFaded.Native.NativeShortcut]::Fail=$false}
   Assert $installFailed 'Installer hid shortcut failure'
   Assert (!(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'CodexNativeGuard\.pet-integration'))) 'Failed installer retained guard integration marker'
   Assert (!$global:protectionTestregistry[$approvalKey].ContainsKey('CodexToolsPet')) 'Failed installation left pet auto-start disabled'
   Assert ($global:protectionTestregistry[$runKey].CodexSunFaded -like 'explorer.exe shell:AppsFolder*') 'Failed installation lost native app login'
   Assert ($global:protectionTestregistry[$runKey].UnrelatedApp -eq 'keep-run' -and $global:protectionTestregistry[$approvalKey].UnrelatedApp -eq 'keep-approval') 'Failed installer changed unrelated startup state'
   $results+=@{case='integrated_install_desktop_failure_rollback';passed=$true}
   continue
  }
  & (Join-Path $installerSource 'Install-FixedConfig.ps1') -IntegratePet:$integrate -AssetDirectory $publicAssets | Out-Null
  $installedConfig=Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'CodexSunFaded\config.json') -Raw | ConvertFrom-Json
  Assert ($installedConfig.launchPetAtLogin -eq $integrate) 'Pet integration was not optional'
  Assert ((Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.codex-plugins\.pet-integration')) -eq $integrate) 'Independent guard lost optional pet integration'
  Assert ($global:protectionTestregistry[$runKey].UnrelatedApp -eq 'keep-run') 'Installer erased unrelated login'
  Assert ($global:protectionTestregistry[$approvalKey].UnrelatedApp -eq 'keep-approval') 'Installer erased unrelated approval'
  Assert ($global:protectionTestregistry[$runKey].CodexSunFaded -like '*start.vbs*') 'Installer did not configure guarded startup'
  if($publicAssets) {
   Assert ([IO.File]::ReadAllText((Join-Path $env:LOCALAPPDATA 'CodexSunFaded\SunFaded.png')) -eq 'user-supplied-SunFaded.png') 'Installer did not copy the selected background'
   Assert ([IO.File]::ReadAllText((Join-Path $env:LOCALAPPDATA 'CodexSunFaded\Sidebar.css')) -eq 'user-supplied-Sidebar.css') 'Installer did not copy the selected sidebar'
  }
  $results+=@{case=('fresh_install_pet_'+$integrate);passed=$true}
 }
} finally { $env:LOCALAPPDATA=$originalLocalAppData;$env:USERPROFILE=$originalUserProfile }

& (Join-Path $testRoot 'Set-ThemeEnabled.ps1')
Assert (Test-Path -LiteralPath (Join-Path $testRoot '.theme-disabled')) 'Disable failed with corrupt config'
$results+=@{case='disable_corrupt_config';passed=$true}
New-Item -ItemType Directory -Path (Join-Path (Split-Path $PSScriptRoot -Parent) '记录') -Force | Out-Null
$results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) '记录\startup-protection-verification.json') -Encoding UTF8
Write-Output "Passed $($results.Count) isolated startup fault scenarios. No real app/registry/shortcut actions. Fixture: $testRoot"
