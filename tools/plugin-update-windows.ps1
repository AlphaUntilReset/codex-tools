param([Parameter(Mandatory=$true)][string]$Action,[Parameter(Mandatory=$true)][string]$InputFile)
$ErrorActionPreference='Stop'
$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$inputData=Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json
$runKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$approvalKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$nativeShortcutSource=Join-Path $PSScriptRoot 'Native-Shortcut.cs'
if(!(Test-Path -LiteralPath $nativeShortcutSource)){$nativeShortcutSource=Join-Path (Split-Path $PSScriptRoot -Parent) 'components\codex-wallpaper\源码\Native-Shortcut.cs'}
if(Test-Path -LiteralPath $nativeShortcutSource){Add-Type -Path $nativeShortcutSource}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Text;
public static class PluginUpdateNative {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern int GetCurrentPackageFullName(ref uint size,IntPtr name);
 public static int IdentityStatus(){uint size=0;return GetCurrentPackageFullName(ref size,IntPtr.Zero);}
 [DllImport("shell32.dll", SetLastError=true)] static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string cmd,out int count);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
 [UnmanagedFunctionPointer(CallingConvention.Winapi)] public delegate bool EnumCallback(IntPtr h,IntPtr p);
 [DllImport("user32.dll")] static extern bool EnumWindows(EnumCallback cb,IntPtr p);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern int GetPackageFullName(IntPtr process,ref uint size,StringBuilder name);
 public static string PackageIdentity(uint pid) {
  var process=OpenProcess(0x1000,false,pid);if(process==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
  try {uint size=0;int code=GetPackageFullName(process,ref size,null);if(code==15700)return null;if(code!=122)throw new System.ComponentModel.Win32Exception(code);var text=new StringBuilder((int)size);code=GetPackageFullName(process,ref size,text);if(code!=0)throw new System.ComponentModel.Win32Exception(code);return text.ToString();}
  finally{CloseHandle(process);}
 }
 public static string[] Arguments(string text) {
  if(String.IsNullOrWhiteSpace(text))return new string[0];
  int n;var p=CommandLineToArgvW(text,out n);if(p==IntPtr.Zero)throw new Exception("Command line parse failed");
  try{var a=new string[n];for(int i=0;i<n;i++)a[i]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(p,i*IntPtr.Size));return a;}finally{LocalFree(p);}
 }
 public static bool Visible(int processId) {
  bool visible=false;EnumWindows((h,p)=>{uint owner;GetWindowThreadProcessId(h,out owner);if(owner==processId&&IsWindowVisible(h))visible=true;return true;},IntPtr.Zero);return visible;
 }
}
'@
function Start-UnpackagedProcess([string]$executable,[string]$arguments,[string]$directory) {
 # A job breakaway alone still inherits a packaged client's MSIX identity.
 # WMI creates a sibling outside both lifetimes; explicitly retain this machine's environment.
 $variables=[string[]]@(Get-ChildItem Env: | ForEach-Object {$_.Name+'='+$_.Value})
 $startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{CreateFlags=[uint32]0x09000000;ShowWindow=[uint16]0;EnvironmentVariables=$variables}
 $command='"'+$executable+'"'+$(if($arguments){' '+$arguments}else{''})
 $created=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$command;CurrentDirectory=$directory;ProcessStartupInformation=$startup}
 if($created.ReturnValue -ne 0){throw ('Independent process creation failed: '+$created.ReturnValue)}
 $identity=[PluginUpdateNative]::PackageIdentity([uint32]$created.ProcessId)
 if($identity){throw ('Independent plugin still has package identity: '+$identity)}
 return @{pid=[int]$created.ProcessId;launchMode='windows-wmi-unpackaged';packageIdentity=$identity}
}
function Read-Startup {
 $values=@()
 foreach($keyPath in @($runKey,$approvalKey)){
  foreach($name in @('CodexToolsPet','CodexSunFaded')){
   $key=Get-Item -LiteralPath $keyPath -ErrorAction SilentlyContinue
   $exists=$key -and $key.GetValueNames() -contains $name
   $value=$null;$kind=$null
   if($exists){$kind=$key.GetValueKind($name).ToString();$value=$key.GetValue($name,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames);if($kind -eq 'Binary'){$value=[Convert]::ToBase64String([byte[]]$value)}}
   $values+=@{key=$keyPath;name=$name;exists=[bool]$exists;kind=$kind;value=$value;args=if($exists -and $kind -in @('String','ExpandString')){@([PluginUpdateNative]::Arguments([Environment]::ExpandEnvironmentVariables([string]$value)))}else{@()}}
  }
 }
 $shortcut=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex.lnk'
 $link=$null
 if(Test-Path -LiteralPath $shortcut){
  $shell=New-Object -ComObject WScript.Shell;$s=$shell.CreateShortcut($shortcut)
  $nativeAppId=$null
  if('SunFaded.Native.NativeShortcut' -as [type]){try{$native=[SunFaded.Native.NativeShortcut]::Read($shortcut);if($native[0] -match '^[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+$'){$nativeAppId=$native[0]}}catch{}}
  $link=@{path=$shortcut;bytes=[Convert]::ToBase64String([IO.File]::ReadAllBytes($shortcut));target=$s.TargetPath;arguments=$s.Arguments;args=@([PluginUpdateNative]::Arguments($s.Arguments));nativeAppId=$nativeAppId}
 }
 return @{values=$values;shortcut=$link}
}
function Get-ManagedProcesses {
 $result=@()
 foreach($p in (Get-CimInstance Win32_Process -Filter "Name='CodexToolsPet.exe' OR Name='node.exe' OR Name='ChatGPT.exe'")){
  if($p.Name -eq 'node.exe' -and $p.CommandLine -notmatch 'theme-runtime\.mjs'){continue}
  $result+=@{pid=[int]$p.ProcessId;parent=[int]$p.ParentProcessId;name=$p.Name;executable=$p.ExecutablePath;startedAt=$p.CreationDate.ToUniversalTime().ToString('o');args=@([PluginUpdateNative]::Arguments($p.CommandLine));visible=if($p.Name -eq 'CodexToolsPet.exe'){[PluginUpdateNative]::Visible($p.ProcessId)}else{$false}}
 }
 return $result
}
function Get-PlanProcesses($plan){
 $found=@()
 foreach($p in (Get-ManagedProcesses)){
  if($plan.id -eq 'desktop-pet'){
   if($p.name -eq 'CodexToolsPet.exe' -and $p.executable -eq (Join-Path $plan.install 'CodexToolsPet.exe')){$found+=$p}
  } elseif($p.name -eq 'node.exe' -and @($p.args | Where-Object {$_ -in @((Join-Path $plan.install 'theme-runtime.mjs'))+$plan.runtimePaths}).Count){$found+=$p}
 }
 return $found
}
function Record-PetStop($plan,$process){
 # Local evidence only. Match both PID and creation time to reject stale sessions.
 if($plan.id -ne 'desktop-pet' -or !$plan.dataDir){return $null}
 $sessions=Join-Path $plan.dataDir 'logs/sessions'
 if(!(Test-Path -LiteralPath $sessions)){return $null}
 foreach($folder in (Get-ChildItem -LiteralPath $sessions -Directory)){
  $manifest=Join-Path $folder.FullName 'session.json'
  if(!(Test-Path -LiteralPath $manifest)){continue}
  try{
   $session=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8|ConvertFrom-Json
   if($session.pid -ne $process.pid){continue}
   $expected=[DateTime]::FromFileTimeUtc([long]$session.created_ticks)
   $actual=[DateTime]::Parse($process.startedAt).ToUniversalTime()
   if([Math]::Abs(($expected-$actual).TotalMilliseconds) -gt 1){continue}
   $target=Join-Path $folder.FullName 'stop-request.json'
   $text=@{reason='plugin-updater-stop';pid=$process.pid;created_ticks=[string]$session.created_ticks;requested_at=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json
   [IO.File]::WriteAllText(($target+'.tmp'),$text,[Text.UTF8Encoding]::new($false))
   Move-Item -LiteralPath ($target+'.tmp') -Destination $target -Force
   return $folder.FullName
  }catch{[Console]::Error.WriteLine('Pet stop evidence unavailable: '+$_.Exception.Message)}
 }
 return $null
}
function Set-StartupState($state,$replaceShortcutFrom=$null){
 if($state.shortcut){
  $currentBytes=if(Test-Path -LiteralPath $state.shortcut.path){[Convert]::ToBase64String([IO.File]::ReadAllBytes($state.shortcut.path))}else{$null}
  if($currentBytes -ne $state.shortcut.bytes){
   if(!$replaceShortcutFrom -or $currentBytes -cne $replaceShortcutFrom){throw 'Desktop shortcut changed concurrently; preserved for review'}
   [IO.File]::WriteAllBytes($state.shortcut.path,[Convert]::FromBase64String($state.shortcut.bytes))
  }
 }
 foreach($item in $state.values){
  if($item.key -notin @($runKey,$approvalKey) -or $item.name -notin @('CodexToolsPet','CodexSunFaded')){throw 'Unmanaged registry value'}
  $key=Get-Item -LiteralPath $item.key -ErrorAction SilentlyContinue
  $exists=$key -and $key.GetValueNames() -contains $item.name
  if(!$item.exists){if($exists){Remove-ItemProperty -LiteralPath $item.key -Name $item.name -ErrorAction Stop};continue}
  $value=$item.value;if($item.kind -eq 'Binary'){$value=[Convert]::FromBase64String($item.value)}
  $current=if($exists){$key.GetValue($item.name,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)}else{$null}
  $same=$exists -and $key.GetValueKind($item.name).ToString() -eq $item.kind
  if($same){if($item.kind -eq 'Binary'){$same=[Convert]::ToBase64String([byte[]]$current) -eq $item.value}else{$same=$current -ceq $value}}
  if(!$same){if(!$key){New-Item -Path $item.key -Force | Out-Null};New-ItemProperty -LiteralPath $item.key -Name $item.name -Value $value -PropertyType $item.kind -Force | Out-Null}
 }
}
switch($Action){
 'inspect' {
  $package=Get-AppxPackage 'OpenAI.Codex' -ErrorAction SilentlyContinue | Select-Object -First 1
  $processes=@(Get-ManagedProcesses);$wallpapers=@()
  $folders=@(Join-Path $env:LOCALAPPDATA 'CodexSunFaded')
  foreach($process in $processes){if($process.name -eq 'node.exe'){foreach($arg in $process.args){if($arg -match '(?:^|[\\/])theme-runtime\.mjs$'){$folders+=Split-Path -Parent $arg}}}}
  foreach($folder in ($folders|Select-Object -Unique)){
   $config=Join-Path $folder 'config.json'
   if(Test-Path -LiteralPath $config){$wallpapers+=@{logical=$folder;physical=(Split-Path -Parent ([SunFaded.Native.NativeShortcut]::PhysicalFile($config)))}}
  }
  @{identityStatus=[PluginUpdateNative]::IdentityStatus();wallpapers=$wallpapers;startup=(Read-Startup);processes=$processes;localAppData=$env:LOCALAPPDATA;petData=if($env:CODEXTOOLS_PET_DATA_DIR){$env:CODEXTOOLS_PET_DATA_DIR}else{Join-Path $env:LOCALAPPDATA 'CodexToolsPet'};appId=if($package){$package.PackageFamilyName+'!App'}else{$null}} | ConvertTo-Json -Depth 12 -Compress
 }
 'startup' { Read-Startup | ConvertTo-Json -Depth 10 -Compress }
 'prepare-entry' {
  $after=$inputData.startup|ConvertTo-Json -Depth 12|ConvertFrom-Json
  $appId=$inputData.machine.appId
  if(!$appId){throw 'Cannot prepare a native shortcut without the registered app identity'}
  $configFile=Join-Path $inputData.machine.wallpaper 'config.json'
  $physicalWallpaper=Split-Path -Parent ([SunFaded.Native.NativeShortcut]::PhysicalFile($configFile))
  $run=$after.values|Where-Object {$_.key -eq $runKey -and $_.name -eq 'CodexSunFaded'}
  $migrateLogin=$run.exists -and ($run.value -match '(?i)(start\.vbs|CodexNativeGuard|CodexSunFaded[\\/]launch\.vbs)')
  if($migrateLogin){$run.value=[SunFaded.Native.NativeShortcut]::LoginCommand($appId);$run.kind='String';$run.args=@([PluginUpdateNative]::Arguments([Environment]::ExpandEnvironmentVariables($run.value)))}
  if($after.shortcut -and ($after.shortcut.nativeAppId -eq $appId -or @($after.shortcut.args|Where-Object {$_ -match '(?i)(?:^|[\\/])start\.vbs$'}).Count)){
   $arguments=if($inputData.disabled){''}else{'--remote-debugging-address=127.0.0.1 --remote-debugging-port='+[int]$inputData.port}
   if($after.shortcut.nativeAppId -ne $appId -or $after.shortcut.arguments -cne $arguments){
    $temporary=Join-Path (Split-Path -Parent $InputFile) 'native-desktop.lnk'
    [SunFaded.Native.NativeShortcut]::Create($temporary,$appId,$arguments)
    $readback=[SunFaded.Native.NativeShortcut]::Read($temporary)
    if($readback[0] -ne $appId -or $readback[1] -cne $arguments){throw 'Native shortcut readback failed'}
    $shell=New-Object -ComObject WScript.Shell;$link=$shell.CreateShortcut($temporary)
    $after.shortcut.bytes=[Convert]::ToBase64String([IO.File]::ReadAllBytes($temporary));$after.shortcut.target=$link.TargetPath
    $after.shortcut.arguments=$arguments;$after.shortcut.args=@([PluginUpdateNative]::Arguments($arguments));$after.shortcut.nativeAppId=$appId
   }
  }
  @{startup=$after;anchor=(Join-Path $env:USERPROFILE '.codex-plugins\start.vbs');physicalWallpaper=$physicalWallpaper;migrateLogin=[bool]$migrateLogin}|ConvertTo-Json -Depth 12 -Compress
 }
 'apply-startup' {
  $now=Read-Startup
  foreach($before in $inputData.before.values){
   $current=$now.values|Where-Object {$_.key -eq $before.key -and $_.name -eq $before.name}
   if($current.exists -ne $before.exists -or $current.kind -cne $before.kind -or $current.value -cne $before.value){throw 'Startup changed concurrently before protected entry migration'}
  }
  if($inputData.before.shortcut -and $now.shortcut.bytes -cne $inputData.before.shortcut.bytes){throw 'Desktop shortcut changed concurrently before protected entry migration'}
  Set-StartupState $inputData.after $inputData.before.shortcut.bytes
  @{ok=$true}|ConvertTo-Json -Compress
 }
 'restore-startup' {
  Set-StartupState $inputData $inputData.expectedShortcutBytes
  @{ok=$true}|ConvertTo-Json -Compress
 }
 'processes' { @{processes=@(Get-PlanProcesses $inputData)} | ConvertTo-Json -Depth 8 -Compress }
 'stop' {
  $recoveryMutex=$null;$recoveryOwned=$false
  try {
  if($inputData.id -eq 'desktop-pet' -and $inputData.dataDir){
   $digest=[Security.Cryptography.SHA256]::Create()
   try{$key=([BitConverter]::ToString($digest.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($inputData.dataDir).TrimEnd('\').ToLowerInvariant())))).Replace('-','').ToLowerInvariant()}finally{$digest.Dispose()}
   $recoveryMutex=[Threading.Mutex]::new($false,('Local\CodexToolsPet.Recovery.'+$key))
   try{$recoveryOwned=$recoveryMutex.WaitOne(12000)}catch [Threading.AbandonedMutexException]{$recoveryOwned=$true}
   if(!$recoveryOwned){throw 'Recovery is still in progress; no program stopped or replaced'}
   # Also cancel an observer that has already recorded an exit but has not yet
   # attempted recovery. Keep the original monitor.json exit evidence intact.
   $sessions=Join-Path $inputData.dataDir 'logs/sessions'
   if(Test-Path -LiteralPath $sessions){foreach($folder in (Get-ChildItem -LiteralPath $sessions -Directory)){
    try{
     $session=Get-Content -LiteralPath (Join-Path $folder.FullName 'session.json') -Raw -Encoding UTF8|ConvertFrom-Json
     if($session.executable -ne (Join-Path $inputData.install 'CodexToolsPet.exe')){continue}
     $record=Get-Content -LiteralPath (Join-Path $folder.FullName 'monitor.json') -Raw -Encoding UTF8|ConvertFrom-Json
     $observer=Get-Process -Id $record.pid -ErrorAction SilentlyContinue
     if(!$observer -or !$observer.StartTime -or $observer.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() -ne [string]$record.created_ticks){continue}
     $text=@{reason='plugin-updater-stop';pid=$session.pid;created_ticks=[string]$session.created_ticks;requested_at=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json
     [IO.File]::WriteAllText((Join-Path $folder.FullName 'stop-request.json'),$text,[Text.UTF8Encoding]::new($false))
    }catch{}
   }}
  }
  foreach($p in (Get-PlanProcesses $inputData)){
   $evidence=Record-PetStop $inputData $p
   Stop-Process -Id $p.pid -Force -ErrorAction Stop
   $deadline=[DateTime]::UtcNow.AddSeconds(12)
   while((Get-Process -Id $p.pid -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
   if(Get-Process -Id $p.pid -ErrorAction SilentlyContinue){throw 'Managed process did not stop'}
   # Allow the independent recorder to flush and release the installed image.
   if($evidence){
    $deadline=[DateTime]::UtcNow.AddSeconds(3)
    do{
     $monitor=Join-Path $evidence 'monitor.json'
     $record=if(Test-Path -LiteralPath $monitor){try{Get-Content -LiteralPath $monitor -Raw -Encoding UTF8|ConvertFrom-Json}catch{$null}}else{$null}
     if($record.state -in @('exited','observer-error')){
      $observer=Get-Process -Id $record.pid -ErrorAction SilentlyContinue
      $observerStart=if($observer){$observer.StartTime}else{$null}
      if(!$observer -or ($observerStart -and $observerStart.ToUniversalTime().ToFileTimeUtc().ToString() -ne [string]$record.created_ticks)){break}
     }
     Start-Sleep -Milliseconds 50
    }while([DateTime]::UtcNow -lt $deadline)
   }
  }
  @{ok=$true} | ConvertTo-Json -Compress
  } finally {
   if($recoveryOwned){$recoveryMutex.ReleaseMutex()}
   if($recoveryMutex){$recoveryMutex.Dispose()}
  }
 }
 'start' {
  if($inputData.id -eq 'desktop-pet'){
   foreach($name in @('QT_QPA_PLATFORM','QT_PLUGIN_PATH','QML2_IMPORT_PATH','PYTHONHOME','PYTHONPATH')){Remove-Item -LiteralPath ('Env:\'+$name) -ErrorAction SilentlyContinue}
   $env:CODEXTOOLS_PET_DATA_DIR=$inputData.dataDir
   $started=Start-UnpackagedProcess (Join-Path $inputData.install 'CodexToolsPet.exe') '' $inputData.install
  } else {
   if($inputData.disabled){@{pid=$null}|ConvertTo-Json -Compress;break}
   $env:SUNFADED_CDP_PORT=[string]$inputData.port
   $started=Start-UnpackagedProcess $inputData.node ('"'+(Join-Path $inputData.install 'theme-runtime.mjs')+'"') $inputData.install
  }
  $started | ConvertTo-Json -Compress
 }
 'extract' {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $destination=[IO.Path]::GetFullPath($inputData.destination)
  if(Test-Path -LiteralPath $destination){throw 'Extraction target already exists'}
  $zip=[IO.Compression.ZipFile]::OpenRead($inputData.archive)
  try {
   $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
   $bytes=0L
   foreach($entry in $zip.Entries){
    $name=$entry.FullName.Replace('\','/');$parts=$name.TrimEnd('/').Split('/')
    if(!$name -or $name.StartsWith('/') -or @($parts | Where-Object {!$_ -or $_ -in @('.','..') -or $_ -match '[:<>"|?*]' -or $_ -match '[. ]$' -or $_ -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)'}).Count){throw 'Unsafe archive entry'}
    $target=[IO.Path]::GetFullPath((Join-Path $destination $name))
    if(!$target.StartsWith($destination+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Archive entry outside extraction target'}
    if((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000){throw 'Archive link refused'}
    if(!$name.EndsWith('/') -and !$seen.Add($name)){throw 'Duplicate archive entry'}
    $bytes+=$entry.Length;if($bytes -gt 2147483648L){throw 'Archive exceeds supported size'}
   }
   New-Item -ItemType Directory -Path $destination | Out-Null
   foreach($entry in $zip.Entries){
    $target=Join-Path $destination $entry.FullName
    if($entry.FullName.EndsWith('/')){New-Item -ItemType Directory -Path $target -Force | Out-Null;continue}
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$target,$false)
   }
   @{files=$seen.Count;bytes=$bytes} | ConvertTo-Json -Compress
  } finally {$zip.Dispose()}
 }
 default {throw 'Unknown updater adapter operation'}
}
