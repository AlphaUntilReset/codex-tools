$ErrorActionPreference='Stop'
# npm can inherit PowerShell 7's module path while launching Windows PowerShell.
# Use this host's built-ins, then resolve hashing before mocking Get-Command.
$env:PSModulePath=Join-Path $PSHOME 'Modules'
$null=Get-Command Get-FileHash
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('CodexToolsSetup-'+[guid]::NewGuid().ToString('N'))
$testSource=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'start.ps1'))
$testBytes=[IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'start.ps1'))
if([BitConverter]::ToString($testBytes[0..2]) -ne 'EF-BB-BF'){throw 'Setup requires UTF-8 BOM for non-ASCII paths under Windows PowerShell 5.1'}
$testTokens=$null;$testErrors=$null
$testAst=[Management.Automation.Language.Parser]::ParseInput($testSource,[ref]$testTokens,[ref]$testErrors)
if($testErrors.Count){throw 'Setup script has parse errors'}
function Assert($Condition,[string]$Message){if(!$Condition){throw $Message}}
foreach($wrapper in @(@{name='启动桌宠.cmd';mode='pet'},@{name='安装背景.cmd';mode='wallpaper'})) {
 $wrapperText=[IO.File]::ReadAllText((Join-Path (Split-Path -Parent $PSScriptRoot) $wrapper.name))
 Assert ($wrapperText.Contains('-File "%~dp0tools\start.ps1" -Mode '+$wrapper.mode)) 'Command wrapper lost its quoted script path or mode'
}
$testFunctions=$testAst.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Pick-Image','Invoke-Checked')},$true)
$testOriginalInvoke=($testFunctions|Where-Object Name -eq 'Invoke-Checked').Extent.Text
# Keep the production call sites and their argument binding; replace only UI/process bodies.
foreach($testFunction in ($testFunctions|Sort-Object {$_.Extent.StartOffset} -Descending)) {
 $replacement=if($testFunction.Name -eq 'Pick-Image') {@'
function Pick-Image([string]$Title,[string]$Filter) {
 $global:setupPicks++
 if($global:setupCancel){throw 'Image selection cancelled'}
 return $global:setupImage
}
'@} else {@'
function Invoke-Checked([string]$Program,[string[]]$Arguments) {
 $global:setupCommands+=,[pscustomobject]@{program=$Program;arguments=$Arguments}
 if($global:setupFail -and $Arguments -contains $global:setupFail){throw 'Mock command failure'}
 if($Arguments -contains 'venv'){New-Item -ItemType Directory -Path (Join-Path $Arguments[-1] 'Scripts') -Force|Out-Null}
 if($Arguments -contains 'init'){New-Item -ItemType Directory -Path (Split-Path -Parent $global:setupPack) -Force|Out-Null;[IO.File]::WriteAllText($global:setupPack,'user pack')}
}
'@}
 $testSource=$testSource.Substring(0,$testFunction.Extent.StartOffset)+$replacement+$testSource.Substring($testFunction.Extent.EndOffset)
}
function Get-Command {param($Name,$ErrorAction);if(!$global:setupNoPython){[pscustomobject]@{Source='C:\Program Files\Test Python\py.exe'}}}
function Start-Process {param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle);$global:setupLaunches+=,[pscustomobject]@{file=$FilePath;arguments=$ArgumentList;cwd=$WorkingDirectory;style=$WindowStyle}}
$testResults=@()
$testOriginalUserProfile=$env:USERPROFILE
try {
$env:USERPROFILE=Join-Path $testRoot 'isolated-user'
foreach($case in @('pet_fresh','pet_existing','pet_pip_failure','pet_invalid_pack','pet_cancel','wallpaper','wallpaper_cancel','wallpaper_build_failure','wallpaper_no_python')) {
 $caseRoot=Join-Path $testRoot ($case+' 配套 with spaces')
 $caseTools=Join-Path $caseRoot 'tools'
 $casePet=Join-Path $caseRoot 'components\desktop-pet'
 $caseWallpaper=Join-Path $caseRoot 'components\codex-wallpaper\源码'
 foreach($directory in @($caseTools,$casePet,$caseWallpaper)){New-Item -ItemType Directory -Path $directory -Force|Out-Null}
 [IO.File]::WriteAllText((Join-Path $caseTools 'start.ps1'),$testSource,[Text.UTF8Encoding]::new($true))
 [IO.File]::WriteAllText((Join-Path $casePet 'requirements.lock.txt'),'test dependency lock')
 $caseIdentity=[IO.Path]::GetFullPath($caseRoot).TrimEnd([char[]]'\/').ToLowerInvariant()
 $caseHasher=[Security.Cryptography.SHA256]::Create()
 try {$caseKey=([BitConverter]::ToString($caseHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($caseIdentity)))).Replace('-','').Substring(0,12).ToLowerInvariant()}finally{$caseHasher.Dispose()}
 $caseVenv=Join-Path (Join-Path $env:USERPROFILE '.codex-tools\v') $caseKey
 [IO.File]::WriteAllText((Join-Path $caseWallpaper 'Install-FixedConfig.ps1'),'param([string]$AssetDirectory);$global:setupInstalls+=,$AssetDirectory')
 $global:setupImage=Join-Path $testRoot '自备 picture with spaces.png'
 $global:setupPack=Join-Path $casePet 'user-assets\pet\pack.json'
 $global:setupCommands=@();$global:setupLaunches=@();$global:setupInstalls=@();$global:setupPicks=0
 $global:setupCancel=$case -like '*_cancel';$global:setupNoPython=$case -eq 'wallpaper_no_python'
 $global:setupFail=switch($case){'pet_pip_failure'{'pip'};'pet_invalid_pack'{'validate'};'wallpaper_build_failure'{'--background'};default{''}}
 if($case -eq 'pet_existing') {
  New-Item -ItemType Directory -Path (Join-Path $caseVenv 'Scripts') -Force|Out-Null
  [IO.File]::WriteAllText((Join-Path $caseVenv 'Scripts\python.exe'),'not executable')
  [IO.File]::WriteAllText((Join-Path $caseVenv 'requirements.sha256'),(Get-FileHash -LiteralPath (Join-Path $casePet 'requirements.lock.txt')).Hash)
  New-Item -ItemType Directory -Path (Split-Path -Parent $global:setupPack) -Force|Out-Null
  [IO.File]::WriteAllText($global:setupPack,'existing user pack')
 }
 if($case -eq 'pet_fresh') {
  # An old checkout-local environment may contain a partial Qt installation.
  New-Item -ItemType Directory -Path (Join-Path $casePet '.venv\Scripts') -Force|Out-Null
  [IO.File]::WriteAllText((Join-Path $casePet '.venv\Scripts\python.exe'),'old incomplete environment')
 }
 $mode=if($case -like 'pet_*'){'pet'}else{'wallpaper'}
 $failed=$false;$failureDetail='none'
 try {& (Join-Path $caseTools 'start.ps1') -Mode $mode} catch {$failed=$true;$failureDetail=($_.Exception.Message+' '+$_.ScriptStackTrace)}
 $expectedFailure=$case -in @('pet_pip_failure','pet_invalid_pack','pet_cancel','wallpaper_cancel','wallpaper_build_failure','wallpaper_no_python')
 Assert ($failed -eq $expectedFailure) "Wrong outcome for ${case}: expectedFailure=$expectedFailure; actualFailure=$failureDetail"
 if($expectedFailure){
  $expectedMessage=if($case -like '*_cancel'){'Image selection cancelled'}elseif($case -eq 'wallpaper_no_python'){'Install Python 3'}else{'Mock command failure'}
  Assert ($failureDetail.Contains($expectedMessage)) "Unexpected failure for ${case}: $failureDetail"
  Assert ($global:setupLaunches.Count -eq 0 -and $global:setupInstalls.Count -eq 0) "Failure launched or installed in $case"
 }
 elseif($mode -eq 'pet') {
  Assert ($global:setupLaunches.Count -eq 1) "Pet was not launched in $case"
  Assert ($global:setupLaunches[0].file -eq (Join-Path $caseVenv 'Scripts\pythonw.exe')) 'Pet did not use the short user-profile environment'
  Assert ($global:setupLaunches[0].arguments -eq ('"'+(Join-Path $casePet 'run_pet.py')+'"')) 'Pet entry path was not quoted'
  Assert ($global:setupLaunches[0].cwd -eq $casePet -and $global:setupLaunches[0].style -eq 'Hidden') 'Pet working directory or window style changed'
  Assert ($global:setupCommands[-1].arguments[-1] -eq $global:setupPack) 'Pack validation lost its path'
  if($case -eq 'pet_existing'){Assert ($global:setupCommands.Count -eq 1 -and $global:setupPicks -eq 0) 'Existing pack or dependencies were replaced'}
  else {
   Assert ($global:setupCommands[0].arguments.Count -eq 4 -and $global:setupCommands[2].arguments[-1] -eq $global:setupImage) 'Python or image arguments were split'
   Assert ($global:setupCommands[0].arguments[-1] -eq $caseVenv) 'Environment was created in the checkout or wrong user location'
   Assert ([IO.File]::ReadAllText((Join-Path $caseVenv 'requirements.sha256')) -eq (Get-FileHash -LiteralPath (Join-Path $casePet 'requirements.lock.txt')).Hash) 'Dependency stamp was not saved with the short environment'
   Assert ([IO.File]::ReadAllText((Join-Path $casePet '.venv\Scripts\python.exe')) -eq 'old incomplete environment') 'Previous checkout environment was modified'
  }
 } else {
  Assert ($global:setupInstalls.Count -eq 1 -and $global:setupLaunches.Count -eq 0 -and $global:setupPicks -eq 3) 'Wallpaper workflow skipped input or launched a pet'
  Assert ($global:setupCommands[0].arguments.Count -eq 10 -and $global:setupCommands[0].arguments[3] -eq $global:setupImage) 'Wallpaper paths were split or Unicode source path changed'
  Assert ($global:setupInstalls[0] -eq (Join-Path $caseRoot 'components\codex-wallpaper\build\own-images')) 'Installer received the wrong local output directory'
 }
 $testResults+=$case
}
} finally { $env:USERPROFILE=$testOriginalUserProfile }
# Separately exercise the unchanged production error propagation and argument forwarding.
Invoke-Expression $testOriginalInvoke
function Test-SetupProgram {$global:setupNativeArguments=$args;$global:LASTEXITCODE=$global:setupNativeExit}
$global:setupNativeExit=0
Invoke-Checked 'Test-SetupProgram' @('--image','path with spaces and 非ASCII.png')
Assert ($global:setupNativeArguments.Count -eq 2 -and $global:setupNativeArguments[1] -eq 'path with spaces and 非ASCII.png') 'Native invocation lost arguments'
$global:setupNativeExit=2;$failed=$false
try {Invoke-Checked 'Test-SetupProgram' @('validate')} catch {$failed=$true}
Assert $failed 'Native nonzero exit did not stop setup'
Write-Output "Passed $($testResults.Count + 2) isolated setup scenarios. No real Python, dialog, app, startup or install actions."
