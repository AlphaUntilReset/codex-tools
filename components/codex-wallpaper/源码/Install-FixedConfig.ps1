param([switch]$IntegratePet, [string]$AssetDirectory, [switch]$Plan)
$ErrorActionPreference = 'Stop'
$taskSource = $PSScriptRoot
$taskInstall = Join-Path $env:LOCALAPPDATA 'CodexSunFaded'
$taskGuardDir = Join-Path $env:USERPROFILE '.codex-plugins'
$taskBackup = Join-Path $taskInstall 'backup'
$taskRun = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$taskApproved = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$taskPetSettings = Join-Path $env:LOCALAPPDATA 'CodexToolsPet\settings.json'
$taskPetExe = Join-Path $env:LOCALAPPDATA 'Programs\CodexToolsPet\CodexToolsPet.exe'
$taskLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex.lnk'
$taskNode = (Get-Command node -ErrorAction Stop).Source
$taskBefore = if (Test-Path -LiteralPath $taskRun) { Get-ItemProperty -LiteralPath $taskRun } else { [pscustomobject]@{} }
if (!(Get-AppxPackage 'OpenAI.Codex')) { throw 'Install the Windows Codex app first.' }
if ([int]((& $taskNode --version).TrimStart('v').Split('.')[0]) -lt 22) { throw 'Node.js 22 or later is required.' }
if ($taskBefore.PSObject.Properties['CodexSunFaded']) { throw 'A fixed startup is already installed. Inspect it before reinstalling.' }
if (Test-Path -LiteralPath (Join-Path $taskInstall 'config.json')) { throw 'Existing local configuration found. Installation will not overwrite it.' }
$taskOriginalPetRun = $null
if ($IntegratePet) {
$taskOriginalPetRun = $taskBefore.CodexToolsPet
if ($taskOriginalPetRun -notmatch '^"([^"\r\n]+\.exe)" --launch-codex$') { throw 'Unexpected existing pet startup; no startup settings were changed.' }
$taskPetExe=$Matches[1]
$taskPetHash = (Get-FileHash -LiteralPath $taskPetSettings).Hash
$taskPetConfig = Get-Content -LiteralPath $taskPetSettings -Raw | ConvertFrom-Json
if (-not $taskPetConfig.autostart) { throw 'Unexpected pet autostart preference.' }
}
$taskApprovedProperty = if (Test-Path $taskApproved) { (Get-ItemProperty $taskApproved).PSObject.Properties['CodexToolsPet'] } else { $null }
$taskFiles = @('SunFaded.css','Sidebar.css','SidebarPalette.svg','SunFaded.png','theme-runtime.mjs','instance-lock.mjs','Fixed-Launcher.ps1','PackageActivation.cs','Native-Shortcut.cs','launch.vbs','Emergency-Start.ps1','Set-ThemeEnabled.ps1','Restore-Startup.ps1','ATTRIBUTION.txt')
function Get-InstallationInput([string]$Name) {
    if ($AssetDirectory -and $Name -in @('SunFaded.css','Sidebar.css','SidebarPalette.svg','SunFaded.png')) { return Join-Path $AssetDirectory $Name }
    if ($Name -eq 'SunFaded.png') { return Join-Path (Split-Path $taskSource -Parent) '图片\SunFaded.png' }
    return Join-Path $taskSource $Name
}
foreach ($taskFile in $taskFiles + @('Native-Guard.vbs')) {
    $taskInputFile = Get-InstallationInput $taskFile
    if (!(Test-Path -LiteralPath $taskInputFile -PathType Leaf)) { throw "Missing installation input: $taskInputFile. Run prepare_images.py, then pass its output with -AssetDirectory." }
}
if ($Plan) {
    [pscustomobject]@{ install=$taskInstall; assets=$AssetDirectory; desktopShortcut=$taskLink; loginEntry='CodexSunFaded'; integratePet=[bool]$IntegratePet; files=$taskFiles; planOnly=$true } | ConvertTo-Json -Depth 4
    return
}
New-Item -ItemType Directory -Path $taskBackup -Force | Out-Null
if ($IntegratePet) { Copy-Item -LiteralPath $taskPetSettings -Destination (Join-Path $taskBackup 'pet-settings-original.json') }
if (Test-Path -LiteralPath $taskLink) { Copy-Item -LiteralPath $taskLink -Destination (Join-Path $taskBackup 'Codex-original.lnk') }
[pscustomobject]@{petRun=$taskOriginalPetRun;approvedExisted=[bool]$taskApprovedProperty;approvedBytes=if ($taskApprovedProperty) { [Convert]::ToBase64String([byte[]]$taskApprovedProperty.Value) } else { $null };desktopShortcut=$taskLink;petSettings=$taskPetSettings;guardDir=$taskGuardDir;installedAt=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskBackup 'startup-original.json') -Encoding UTF8
foreach ($taskFile in $taskFiles) {
    $taskInputFile = Get-InstallationInput $taskFile
    Copy-Item -LiteralPath $taskInputFile -Destination (Join-Path $taskInstall $taskFile)
}
[pscustomobject]@{enabled=$true;port=9228;node=$taskNode;launchPetAtLogin=[bool]$IntegratePet;pet=$taskPetExe;image='SunFaded.png';backgroundOpacity=0.15;sidebarEnabled=$true;sidebarOpacity=0.30} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskInstall 'config.json') -Encoding UTF8
if ($IntegratePet -and (Get-FileHash -LiteralPath $taskPetSettings).Hash -ne $taskPetHash) { throw 'Pet preferences changed concurrently; installation stopped before startup edits.' }
try {
# Keep the pet's config and Run command intact. Windows skips its duplicate login entry;
# the combined launcher still starts it, after launching themed Codex.
if ($IntegratePet) {
if (!(Test-Path -LiteralPath $taskApproved)) { New-Item -Path $taskApproved | Out-Null }
$taskDisabled = [byte[]](3,0,0,0) + [BitConverter]::GetBytes([long][DateTime]::UtcNow.ToFileTimeUtc())
New-ItemProperty -LiteralPath $taskApproved -Name 'CodexToolsPet' -Value $taskDisabled -PropertyType Binary -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $taskInstall '.pet-integration'),'enabled')
}
if (!(Test-Path -LiteralPath $taskRun)) { New-Item -Path $taskRun | Out-Null }
New-Item -ItemType Directory -Path $taskGuardDir -Force | Out-Null
if (!('SunFaded.Native.NativeShortcut' -as [type])) { Add-Type -Path (Join-Path $taskSource 'Native-Shortcut.cs') }
$taskPhysicalInstall=Split-Path -Parent ([SunFaded.Native.NativeShortcut]::PhysicalFile((Join-Path $taskInstall 'config.json')))
$taskGuardText=[IO.File]::ReadAllText((Join-Path $taskSource 'Native-Guard.vbs'))
foreach($taskBinding in @(@('%LOCALAPPDATA%\CodexSunFaded',$taskPhysicalInstall),@('%LOCALAPPDATA%\CodexSunFaded\.pet-integration',(Join-Path $taskPhysicalInstall '.pet-integration')),@('%LOCALAPPDATA%\CodexNativeGuard\.pet-integration',(Join-Path $taskGuardDir '.pet-integration')),@('%LOCALAPPDATA%\Programs\CodexToolsPet\CodexToolsPet.exe',$taskPetExe))) {
    $taskGuardText=$taskGuardText.Replace(('shell.ExpandEnvironmentStrings("'+$taskBinding[0]+'")'),('"'+$taskBinding[1].Replace('"','""')+'"'))
}
[IO.File]::WriteAllText((Join-Path $taskGuardDir 'start.vbs'),$taskGuardText,[Text.UnicodeEncoding]::new($false,$true))
if ([SunFaded.Native.NativeShortcut]::PhysicalFile((Join-Path $taskGuardDir 'start.vbs')) -ne (Join-Path $taskGuardDir 'start.vbs')) { throw 'Startup guard was redirected; preserve original entries instead of installing an invisible script.' }
if ($IntegratePet) {
    [IO.File]::WriteAllText((Join-Path $taskGuardDir '.pet-integration'),'enabled')
} else {
    foreach ($taskMarker in @((Join-Path $taskGuardDir '.pet-integration'),(Join-Path $taskInstall '.pet-integration'))) {
        if (Test-Path -LiteralPath $taskMarker) { Remove-Item -LiteralPath $taskMarker -Force -ErrorAction Stop }
    }
}
$taskAppId=(Get-AppxPackage 'OpenAI.Codex' | Select-Object -First 1).PackageFamilyName+'!App'
$taskStartupCommand = [SunFaded.Native.NativeShortcut]::LoginCommand($taskAppId)
New-ItemProperty -LiteralPath $taskRun -Name 'CodexSunFaded' -Value $taskStartupCommand -PropertyType String -Force | Out-Null
[SunFaded.Native.NativeShortcut]::Create($taskLink,$taskAppId,'--remote-debugging-address=127.0.0.1 --remote-debugging-port=9228')
} catch {
    $taskFailure = $_
    & (Join-Path $taskInstall 'Restore-Startup.ps1')
    throw $taskFailure
}
Write-Output "Installed fixed configuration: $taskInstall"
& (Join-Path $taskInstall 'Fixed-Launcher.ps1') -Login -Plan
