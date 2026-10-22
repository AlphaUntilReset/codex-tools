$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'Native-Shortcut.cs')
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('CodexNativeEntry-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
function Assert($ok,$message){if(!$ok){throw $message}}
$checks=@()
# No real client is launched. Only this benign test executable captures fallback.
$capture=Join-Path $fixture 'capture.exe'
Add-Type -TypeDefinition 'public static class CaptureNative {public static void Main(string[] args){System.IO.File.WriteAllText(System.Environment.GetEnvironmentVariable("CODEX_TEST_CAPTURE"),System.String.Join(" ",args));}}' -OutputAssembly $capture -OutputType ConsoleApplication
$originalProfile=$env:USERPROFILE
try {
 $env:USERPROFILE=Join-Path $fixture 'user'
 $anchor=Join-Path $env:USERPROFILE '.codex-plugins\start.vbs'
 $appId='OpenAI.Codex_2p2nqsd0c76g0!App'
 $command=[SunFaded.Native.NativeShortcut]::LoginCommand($appId)
 Assert ($command.Length -le 260) 'Login command exceeds Windows limit'
 $command=$command.Replace('explorer.exe shell:AppsFolder\'+$appId,('"'+$capture+'" shell:AppsFolder\'+$appId))
 foreach($case in @('missing_guard_directory','corrupt_guard','healthy_guard')){
  $env:CODEX_TEST_CAPTURE=Join-Path $fixture ($case+'.txt')
  if($case -eq 'corrupt_guard'){New-Item -ItemType Directory -Path (Split-Path -Parent $anchor) -Force | Out-Null;[IO.File]::WriteAllText($anchor,'This is not valid VBScript !!!')}
  if($case -eq 'healthy_guard'){[IO.File]::WriteAllText($anchor,'WScript.Quit 0')}
  $commandBody=$command.Substring($command.IndexOf('/c ')+3)
  & $env:ComSpec /d /c $commandBody
  Assert ((Test-Path -LiteralPath $env:CODEX_TEST_CAPTURE) -eq ($case -ne 'healthy_guard')) ($case+' native fallback mismatch')
  if($case -ne 'healthy_guard'){Assert ((Get-Content -LiteralPath $env:CODEX_TEST_CAPTURE -Raw) -eq ('shell:AppsFolder\'+$appId)) 'Fallback did not preserve the registered app identity'}
  $checks+=@{case=$case;passed=$true}
 }
} finally {$env:USERPROFILE=$originalProfile;Remove-Item Env:\CODEX_TEST_CAPTURE -ErrorAction SilentlyContinue}
$package=Get-AppxPackage OpenAI.Codex|Select-Object -First 1
if($package){
 $link=Join-Path $fixture 'native-desktop.lnk'
 $appId=$package.PackageFamilyName+'!App'
 $arguments='--remote-debugging-address=127.0.0.1 --remote-debugging-port=19288'
 [SunFaded.Native.NativeShortcut]::Create($link,$appId,$arguments)
 $read=[SunFaded.Native.NativeShortcut]::Read($link)
 Assert ($read[0] -eq $appId -and $read[1] -ceq $arguments) 'Native shortcut does not preserve app identity or arguments'
 Assert ([SunFaded.Native.NativeShortcut]::PhysicalFile($link) -eq $link) 'Test shortcut was redirected'
 # The activation identity has no executable-version or script dependency. Windows
 # may cache package metadata in the PIDL; this readback does not prove activation
 # after an update or replacement of that cached version.
 Assert ($read[0] -notmatch 'WindowsApps|\.exe|\.vbs|\.ps1') 'Native entry retained a plugin dependency'
 $checks+=@{case='native_shortcut_without_any_runtime_files';passed=$true}
}
New-Item -ItemType Directory -Path (Join-Path (Split-Path $PSScriptRoot -Parent) '记录') -Force | Out-Null
$checks|ConvertTo-Json|Set-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) '记录\native-entry-verification.json') -Encoding UTF8
Write-Output "Passed $($checks.Count) native entry checks. Real client and startup state unchanged."
