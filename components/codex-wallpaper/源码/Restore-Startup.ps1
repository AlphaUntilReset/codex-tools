$ErrorActionPreference='Stop'
$taskDir=$PSScriptRoot
$taskRun='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$taskApproved='HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$taskNativeCommand='explorer.exe shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App'
$taskDesktopLink=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex.lnk'
$taskRestoreErrors=@()
# Install native login FIRST; never remove the last working login entry.
try {
    if (!(Test-Path -LiteralPath $taskRun)) { New-Item -Path $taskRun | Out-Null }
    New-ItemProperty -LiteralPath $taskRun -Name 'CodexSunFaded' -Value $taskNativeCommand -PropertyType String -Force | Out-Null
} catch { $taskRestoreErrors+='Native login: '+$_.Exception.Message }
try { [IO.File]::WriteAllText((Join-Path $taskDir '.theme-disabled'),'Restored original startup') } catch { }
try { [IO.File]::WriteAllText((Join-Path $taskDir '.theme-runtime.stop'),'stop') } catch { }
try { & (Join-Path $taskDir 'Emergency-Start.ps1') -NoLaunch } catch { }
$taskOriginal=$null
try { $taskOriginal=Get-Content -LiteralPath (Join-Path $taskDir 'backup\startup-original.json') -Raw | ConvertFrom-Json } catch { }
# Fresh Windows shortcut works even if backups and configuration are damaged.
try {
    $taskShell=New-Object -ComObject WScript.Shell
    $taskShortcut=$taskShell.CreateShortcut($taskDesktopLink)
    $taskShortcut.TargetPath=Join-Path $env:WINDIR 'explorer.exe'
    $taskShortcut.Arguments='shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App'
    $taskShortcut.WorkingDirectory=$env:WINDIR
    $taskShortcut.Description='Codex - Windows original app entry'
    $taskShortcut.Save()
} catch { $taskRestoreErrors+='Desktop shortcut: '+$_.Exception.Message }
try {
    if ($taskOriginal -and $taskOriginal.petRun -and $taskOriginal.approvedExisted -eq $true) {
        $taskOriginalBytes=[Convert]::FromBase64String($taskOriginal.approvedBytes)
        if (!(Test-Path -LiteralPath $taskApproved)) { New-Item -Path $taskApproved | Out-Null }
        New-ItemProperty -LiteralPath $taskApproved -Name 'CodexToolsPet' -Value $taskOriginalBytes -PropertyType Binary -Force | Out-Null
    } elseif ($taskOriginal -and $taskOriginal.petRun -and (Test-Path -LiteralPath $taskApproved)) {
        $taskCurrentApproval=Get-ItemProperty -LiteralPath $taskApproved -ErrorAction Stop
        if ($taskCurrentApproval.PSObject.Properties['CodexToolsPet']) { Remove-ItemProperty -LiteralPath $taskApproved -Name 'CodexToolsPet' -ErrorAction Stop }
    }
} catch { $taskRestoreErrors+='Pet startup approval: '+$_.Exception.Message }
# Retain native Codex login unless the preserved original pet entry supplies it.
try {
    $taskPetRun=(Get-ItemProperty -LiteralPath $taskRun -Name 'CodexToolsPet' -ErrorAction Stop).CodexToolsPet
    $taskExpected=if($taskOriginal){$taskOriginal.petRun}else{$null}
    if ($taskExpected -and $taskPetRun -eq $taskExpected -and $taskExpected -match '^"([^"\r\n]+\.exe)" --launch-codex$' -and (Test-Path -LiteralPath $Matches[1])) {
        $taskPetApproval=if(Test-Path -LiteralPath $taskApproved){(Get-ItemProperty -LiteralPath $taskApproved -ErrorAction Stop).CodexToolsPet}else{$null}
        if (!$taskPetApproval -or $taskPetApproval[0] -eq 2) { Remove-ItemProperty -LiteralPath $taskRun -Name 'CodexSunFaded' -ErrorAction Stop }
    }
} catch { }
try {
    $taskIntegrationMarkers=@((Join-Path $taskDir '.pet-integration'))
    if ($taskOriginal -and $taskOriginal.guardDir) { $taskIntegrationMarkers+=(Join-Path $taskOriginal.guardDir '.pet-integration') }
    # A source/fixture invocation must not modify another installed guard.
    if ($taskDir -eq (Join-Path $env:LOCALAPPDATA 'CodexSunFaded')) {
        $taskIntegrationMarkers+=(Join-Path $env:LOCALAPPDATA 'CodexNativeGuard\.pet-integration')
    }
    foreach ($taskMarker in $taskIntegrationMarkers) {
        if (Test-Path -LiteralPath $taskMarker) { Remove-Item -LiteralPath $taskMarker -Force -ErrorAction Stop }
    }
} catch { $taskRestoreErrors+='Integration marker cleanup: '+$_.Exception.Message }
try {
    @{time=(Get-Date).ToString('o');state=if($taskRestoreErrors.Count){'partial_failure'}else{'restored'};errors=$taskRestoreErrors} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskDir 'restore-status.json') -Encoding UTF8
} catch { }
# Report errors only AFTER every independent recovery step has been attempted.
if ($taskRestoreErrors.Count) { throw ('Recovery incomplete; other recovery steps were attempted. '+($taskRestoreErrors -join '; ')) }
Write-Output 'Native Codex shortcut and login restored. No config/Node required; running client was not restarted.'
