param([switch]$Enable)
$ErrorActionPreference='Stop'
$taskDir=$PSScriptRoot
$taskPath=Join-Path $taskDir 'config.json'
if (!$Enable) {
    & (Join-Path $taskDir 'Emergency-Start.ps1') -NoLaunch
    try {
        $taskConfig=Get-Content -LiteralPath $taskPath -Raw | ConvertFrom-Json
        $taskConfig.enabled=$false
        $taskConfig | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $taskPath -Encoding UTF8
    } catch { }
    return
}
$taskConfig=Get-Content -LiteralPath $taskPath -Raw | ConvertFrom-Json
if (!$taskConfig.node -or !(Test-Path -LiteralPath $taskConfig.node)) { throw 'Node unavailable; theme stays disabled.' }
foreach ($taskFile in @('theme-runtime.mjs','instance-lock.mjs','SunFaded.css','Fixed-Launcher.ps1')) {
    if (!(Test-Path -LiteralPath (Join-Path $taskDir $taskFile))) { throw 'Missing theme resource; theme stays disabled.' }
}
& (Join-Path $taskDir 'Emergency-Start.ps1') -NoLaunch
$taskConfig.enabled=$true
$taskConfig | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $taskPath -Encoding UTF8
Remove-Item -LiteralPath (Join-Path $taskDir '.theme-disabled') -ErrorAction SilentlyContinue
& (Join-Path $taskDir 'Fixed-Launcher.ps1')
