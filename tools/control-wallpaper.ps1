param([Parameter(Mandatory=$true)][ValidateSet('enable','disable','restore','native','recover')][string]$Action,[switch]$Plan)
$ErrorActionPreference='Stop'
# Read a literal path from our installed guard; never execute its text.
$taskFolder=Join-Path $env:LOCALAPPDATA 'CodexSunFaded'
$taskAnchor=Join-Path $env:USERPROFILE '.codex-plugins\start.vbs'
if(Test-Path -LiteralPath $taskAnchor -PathType Leaf){
    $taskText=[IO.File]::ReadAllText($taskAnchor)
    $taskMatch=[regex]::Match($taskText,'(?im)^folder\s*=\s*"((?:[^"]|"")+)"\s*$')
    if($taskMatch.Success){
        $taskCandidate=$taskMatch.Groups[1].Value.Replace('""','"')
        if([IO.Path]::IsPathRooted($taskCandidate) -and (Test-Path -LiteralPath $taskCandidate -PathType Container)){$taskFolder=$taskCandidate}
    }
}
$taskName=switch($Action){'enable'{'Set-ThemeEnabled.ps1'}'disable'{'Set-ThemeEnabled.ps1'}'restore'{'Restore-Startup.ps1'}'native'{'Emergency-Start.ps1'}'recover'{'Fixed-Launcher.ps1'}}
$taskScript=Join-Path $taskFolder $taskName
if($Plan){@{action=$Action;folder=$taskFolder;script=$taskScript;exists=(Test-Path -LiteralPath $taskScript -PathType Leaf);nativeFallback=($Action -eq 'native')}|ConvertTo-Json -Compress;return}
if($Action -eq 'native'){
    if(Test-Path -LiteralPath $taskFolder){
        [IO.File]::WriteAllText((Join-Path $taskFolder '.theme-disabled'),'disabled')
        [IO.File]::WriteAllText((Join-Path $taskFolder '.theme-runtime.stop'),'stop')
    }
    if(Test-Path -LiteralPath $taskScript){try{& $taskScript;return}catch{Write-Warning $_.Exception.Message}}
    Start-Process -FilePath 'explorer.exe' -ArgumentList 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App' -WindowStyle Hidden
    return
}
if(!(Test-Path -LiteralPath $taskScript -PathType Leaf)){throw ('Installed wallpaper control is missing: '+$taskScript)}
if($Action -eq 'enable'){& $taskScript -Enable}elseif($Action -eq 'recover'){& $taskScript -ArmRecovery}else{& $taskScript}
