param([switch]$Login, [switch]$Plan, [switch]$CheckActivation, [switch]$CompanionOnly, [switch]$WaitForExit, [switch]$ArmRecovery, [ValidateRange(1,3600)][int]$WaitSeconds=1800)
$ErrorActionPreference='Stop'
$taskDir=$PSScriptRoot
$taskAppId='OpenAI.Codex_2p2nqsd0c76g0!App'
$taskExe=$null
$taskFamily='OpenAI.Codex_2p2nqsd0c76g0'
$taskWarnings=@()
function Test-PetLoginAllowed {
    try {
        $preference=(Get-ItemProperty -LiteralPath 'HKCU:\Software\CodexToolsPet' -Name 'LoginEnabled' -ErrorAction Stop).LoginEnabled
        if ($preference -is [int] -and $preference -eq 0) { return $false }
    } catch { }
    return $true # An absent/invalid explicit preference preserves legacy integration.
}
function Get-CodexExecutable($package) {
    [xml]$manifest=Get-Content -LiteralPath (Join-Path $package.InstallLocation 'AppxManifest.xml') -Raw -ErrorAction Stop
    $application=@($manifest.Package.Applications.Application | Where-Object {$_.Id -eq 'App'})
    if ($application.Count -ne 1 -or !$application[0].Executable) { throw 'Registered Codex application entry is missing.' }
    $root=[IO.Path]::GetFullPath($package.InstallLocation).TrimEnd('\')+'\'
    $executable=[IO.Path]::GetFullPath((Join-Path $root ([string]$application[0].Executable)))
    if (!$executable.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($executable) -ne '.exe') { throw 'Invalid registered Codex executable.' }
    return $executable
}
$taskConfig=[pscustomobject]@{enabled=$false;port=9228;node='C:\Program Files\nodejs\node.exe';launchPetAtLogin=(Test-Path -LiteralPath (Join-Path $taskDir '.pet-integration'));pet=(Join-Path $env:LOCALAPPDATA 'Programs\CodexToolsPet\CodexToolsPet.exe')}
try {
    $taskLoaded=Get-Content -LiteralPath (Join-Path $taskDir 'config.json') -Raw | ConvertFrom-Json
    if ($null -eq $taskLoaded -or $taskLoaded.enabled -isnot [bool]) { throw 'Invalid configuration.' }
    $taskConfig=$taskLoaded
    if ($taskConfig.enabled -and ([int]$taskConfig.port -lt 1024 -or [int]$taskConfig.port -gt 65535)) { throw 'Invalid theme port.' }
} catch { $taskConfig.enabled=$false; $taskWarnings+='Theme config unavailable; launch without theme.' }
if (Test-Path -LiteralPath (Join-Path $taskDir '.theme-disabled')) { $taskConfig.enabled=$false }
if ($taskConfig.enabled) {
    foreach ($taskFile in @('theme-runtime.mjs','instance-lock.mjs','SunFaded.css')) {
        if (!(Test-Path -LiteralPath (Join-Path $taskDir $taskFile) -PathType Leaf)) { $taskConfig.enabled=$false; $taskWarnings+="Missing resource: $taskFile" }
    }
    if (!$taskConfig.node -or !(Test-Path -LiteralPath $taskConfig.node -PathType Leaf)) { $taskConfig.enabled=$false; $taskWarnings+='Node unavailable.' }
}
function Get-CodexRegistration {
    for ($attempt=0; $attempt -lt 3; $attempt++) {
        try {
            $package=Get-AppxPackage 'OpenAI.Codex' -ErrorAction Stop | Select-Object -First 1
            if (!$package) { throw 'Registered client is temporarily unavailable.' }
            return @{family=$package.PackageFamilyName;executable=(Get-CodexExecutable $package)}
        } catch { if ($attempt -eq 2) { throw }; Start-Sleep -Milliseconds 250 }
    }
}
try {
    $registration=Get-CodexRegistration
    $taskFamily=$registration.family; $taskAppId=$taskFamily+'!App'; $taskExe=$registration.executable
} catch { $taskWarnings+='Package discovery failed; use Windows app entry.' }
function Write-LauncherStatus($state) {
    try { $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskDir 'launcher-status.json') -Encoding UTF8 } catch { }
}
function Get-RunningCodex {
    # A newly registered version can coexist briefly with the old main process.
    $names=@('ChatGPT.exe','codex.exe')
    if ($taskExe) { $names+=([IO.Path]::GetFileName($taskExe)) }
    $filter=(@($names | Select-Object -Unique | ForEach-Object { "Name='"+$_.Replace("'","''")+"'" }) -join ' OR ')
    $family=$taskFamily -split '_',2
    $packagePattern='\\WindowsApps\\'+[regex]::Escape($family[0])+'_[^\\]+_'+[regex]::Escape($family[1])+'\\app\\(?:'+(($names | Select-Object -Unique | ForEach-Object {[regex]::Escape($_)}) -join '|')+')$'
    Get-CimInstance Win32_Process -Filter $filter | Where-Object { (($taskExe -and $_.ExecutablePath -eq $taskExe) -or $_.ExecutablePath -match $packagePattern) -and $_.CommandLine -notmatch '(?:^|\s)--type(?:=|\s)' }
}
function Invoke-PackageActivation([string]$arguments) {
    if (!('SunFaded.Native.PackageActivation' -as [type])) { Add-Type -Path (Join-Path $taskDir 'PackageActivation.cs') }
    [SunFaded.Native.PackageActivation]::Activate($taskAppId,$arguments)
}
function Wait-ForCodex {
    for ($taskAttempt=0; $taskAttempt -lt 12; $taskAttempt++) {
        try { if (Get-RunningCodex) { return $true } } catch { return $false }
        Start-Sleep -Milliseconds 250
    }
    return $false
}
function Start-IndependentCompanion([string]$executable,[string]$arguments) {
    # Job breakaway alone still inherits the client's MSIX package identity.
    if (!('SunFaded.Native.PackageActivation' -as [type])) { Add-Type -Path (Join-Path $taskDir 'PackageActivation.cs') }
    $taskEnvironment=[string[]]@(Get-ChildItem Env: | Where-Object {$_.Name -ne 'SUNFADED_CDP_PORT'} | ForEach-Object {$_.Name+'='+$_.Value})
    $taskEnvironment+=('SUNFADED_CDP_PORT='+$taskConfig.port)
    $taskStartup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{CreateFlags=[uint32]0x09000000;ShowWindow=[uint16]0;EnvironmentVariables=$taskEnvironment}
    $taskCreated=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=('"'+$executable+'" '+$arguments);CurrentDirectory=$taskDir;ProcessStartupInformation=$taskStartup}
    if ($taskCreated.ReturnValue -ne 0) { throw ('Independent companion creation failed: '+$taskCreated.ReturnValue) }
    $taskIdentity=[SunFaded.Native.PackageActivation]::PackageIdentity([uint32]$taskCreated.ProcessId)
    if ($taskIdentity) { throw 'Companion retained a packaged client identity.' }
    return @{pid=[int]$taskCreated.ProcessId;launchMode='windows-wmi-unpackaged';packageIdentity=$taskIdentity}
}
$taskArgs=if ($taskConfig.enabled) { "--remote-debugging-address=127.0.0.1 --remote-debugging-port=$($taskConfig.port)" } else { '' }
if ($Plan -or $CheckActivation) {
    $taskApiValid=$false
    if ($CheckActivation) { try { if (!('SunFaded.Native.PackageActivation' -as [type])) { Add-Type -Path (Join-Path $taskDir 'PackageActivation.cs') }; $taskApiValid=[SunFaded.Native.PackageActivation]::Validate() } catch { } }
    [pscustomobject]@{ActivationMode='WindowsApplicationActivationManager';Fallback='Windows AppsFolder without theme';AppUserModelId=$taskAppId;ActivationApiAvailable=$taskApiValid;CodexExecutable=$taskExe;Arguments=$taskArgs;ThemeEnabled=$taskConfig.enabled;Warnings=$taskWarnings;Runtime=(Join-Path $taskDir 'theme-runtime.mjs');Node=$taskConfig.node;LoginPet=($Login -and !$CompanionOnly -and $taskConfig.launchPetAtLogin -and (Test-PetLoginAllowed));Pet=$taskConfig.pet;WaitForExit=[bool]$WaitForExit;CompanionOnly=[bool]$CompanionOnly;CompanionLaunchMode='windows-wmi-unpackaged'} | ConvertTo-Json
    return
}
$taskRunning=$null
$taskMode='already_running'
$taskActivatedPid=$null
$taskCompanion=$null
$taskRestartRequired=$false
$taskRecoveryMutex=$null
$taskRecoveryOwned=$false
try {
    if ($ArmRecovery) {
        if (!$taskConfig.enabled -or !$taskExe) { throw 'Recovery needs a valid enabled theme and registered client.' }
        $taskPowerShell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $taskRecovery=Start-IndependentCompanion $taskPowerShell ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -WaitForExit -WaitSeconds '+$WaitSeconds)
        @{state='recovery_armed';helper=$taskRecovery;instruction='Fully exit Codex from its tray menu. It will reopen with the theme port automatically.'}|ConvertTo-Json -Depth 5
        return
    }
    try { $taskRunning=Get-RunningCodex } catch { if ($WaitForExit) { throw }; $taskWarnings+='Process lookup unavailable.' }
    if ($WaitForExit) {
        if (!$taskConfig.enabled -or !$taskExe) { throw 'Recovery needs a valid enabled theme and registered client.' }
        $taskHasher=[Security.Cryptography.SHA256]::Create()
        try { $taskMutexSuffix=[BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskDir.ToLowerInvariant()))).Replace('-','') } finally { $taskHasher.Dispose() }
        $taskRecoveryMutex=[Threading.Mutex]::new($false,('Local\CodexSunFaded.Recovery.'+$taskMutexSuffix))
        try { $taskRecoveryOwned=$taskRecoveryMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $taskRecoveryOwned=$true }
        if (!$taskRecoveryOwned) { return }
        $taskDeadline=[DateTime]::UtcNow.AddSeconds($WaitSeconds)
        while ($taskRunning) {
            Write-LauncherStatus @{time=(Get-Date).ToString('o');state='waiting_for_user_exit';pid=@($taskRunning)[0].ProcessId;timeoutSeconds=$WaitSeconds}
            if ([DateTime]::UtcNow -ge $taskDeadline) { throw 'Recovery timed out; no client process was stopped.' }
            if (Test-Path -LiteralPath (Join-Path $taskDir '.theme-disabled')) { throw 'Recovery cancelled because theme was disabled.' }
            Start-Sleep -Milliseconds 1000
            $taskRunning=Get-RunningCodex
        }
        # Servicing may have changed the installed version while we waited.
        $registration=Get-CodexRegistration
        $taskExe=$registration.executable
        $taskFamily=$registration.family
        $taskAppId=$taskFamily+'!App'
    }
    if ($CompanionOnly) {
        $taskMode='companion_only'
    } elseif ($taskRunning -and !$Login) {
        # Desktop activation raises the existing window; it does not restart it.
        # Debug flags cannot be added to an already-running process.
        try {
            $taskActivatedPid=Invoke-PackageActivation ''
            $taskMode='existing_instance_activation'
        } catch {
            $taskWarnings+='Window activation failed; use Windows app entry.'
            Start-Process -FilePath 'explorer.exe' -ArgumentList ('shell:AppsFolder\'+$taskAppId) -WindowStyle Hidden | Out-Null
            $taskMode='existing_instance_shell_fallback'
        }
    } elseif (!$taskRunning) {
        try {
            $taskActivatedPid=Invoke-PackageActivation $taskArgs
            if (!(Wait-ForCodex)) { throw 'App did not remain running.' }
            $taskMode='WindowsApplicationActivationManager'
        } catch {
            $taskWarnings+='Activation failed; retry original app without theme.'
            $taskConfig.enabled=$false
            try {
                $taskActivatedPid=Invoke-PackageActivation ''
                if (!(Wait-ForCodex)) { throw 'Plain activation did not remain running.' }
                $taskMode='plain_package_fallback'
            } catch {
                Start-Process -FilePath 'explorer.exe' -ArgumentList ('shell:AppsFolder\'+$taskAppId) -WindowStyle Hidden | Out-Null
                $taskMode='windows_shell_fallback'
            }
        }
    }
    if ($taskConfig.enabled) {
        # Client activation precedes all companion work; failures stay isolated.
        try {
            $taskListener=Get-NetTCPConnection -LocalPort $taskConfig.port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($taskListener) {
                $taskOwner=Get-RunningCodex | Where-Object {$_.ProcessId -eq $taskListener.OwningProcess}
                if (!$taskOwner) { throw 'Theme port belongs to another application.' }
            }
            if ($taskRunning -and !$taskListener) {
                $taskRestartRequired=$true
                $taskWarnings+='Client is running without its theme port; fully exit and reopen with the desktop shortcut, or use wallpaper recovery.'
            }
            $taskCompanion=Start-IndependentCompanion $taskConfig.node ('"'+(Join-Path $taskDir 'theme-runtime.mjs')+'"')
        } catch { $taskWarnings+='Companion unavailable; client remains open.' }
    }
    Write-LauncherStatus @{time=(Get-Date).ToString('o');state='launcher_completed';activationMode=$taskMode;appUserModelId=$taskAppId;activatedPid=$taskActivatedPid;alreadyRunning=[bool]$taskRunning;themeEnabled=$taskConfig.enabled;login=[bool]$Login;restartRequired=$taskRestartRequired;companion=$taskCompanion;warnings=$taskWarnings}
} catch {
    Write-LauncherStatus @{time=(Get-Date).ToString('o');state='error';message=$_.Exception.Message}
    throw # VBS has its own config-free Windows app fallback.
} finally {
    if ($taskRecoveryOwned) { $taskRecoveryMutex.ReleaseMutex() }
    if ($taskRecoveryMutex) { $taskRecoveryMutex.Dispose() }
    try {
        if ($Login -and !$CompanionOnly -and $taskConfig.launchPetAtLogin -and (Test-PetLoginAllowed) -and $taskConfig.pet -and (Test-Path -LiteralPath $taskConfig.pet)) {
            $taskPetRunning=Get-CimInstance Win32_Process -Filter "name='CodexToolsPet.exe'" | Where-Object { $_.ExecutablePath -eq $taskConfig.pet }
            if (!$taskPetRunning) { $null=Start-IndependentCompanion $taskConfig.pet '' }
        }
    } catch { }
}
