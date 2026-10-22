param([switch]$NoLaunch)
# No config, Node, activation DLL or themed launcher dependency.
$taskDir=$PSScriptRoot
$taskRuntime=Join-Path $taskDir 'theme-runtime.mjs'
try { [IO.File]::WriteAllText((Join-Path $taskDir '.theme-disabled'),'Emergency / recovery: theme disabled') } catch { }
try { [IO.File]::WriteAllText((Join-Path $taskDir '.theme-runtime.stop'),'stop') } catch { }
try {
    $taskPattern='(?:^|\s)"?'+[regex]::Escape($taskRuntime)+'"?(?:\s|$)'
    foreach ($taskProcess in @(Get-CimInstance Win32_Process -Filter "name='node.exe'" -ErrorAction Stop)) {
        if ($taskProcess.CommandLine -match $taskPattern) {
            Wait-Process -Id $taskProcess.ProcessId -Timeout 6 -ErrorAction SilentlyContinue
            $taskCheck=Get-CimInstance Win32_Process -Filter "ProcessId=$($taskProcess.ProcessId)" -ErrorAction Stop
            if ($taskCheck.Name -eq 'node.exe' -and $taskCheck.CommandLine -match $taskPattern) {
                Stop-Process -Id $taskProcess.ProcessId -Force -ErrorAction SilentlyContinue
                Wait-Process -Id $taskProcess.ProcessId -Timeout 2 -ErrorAction SilentlyContinue
            }
        }
    }
} catch { }
# Clear only this theme's nodes in an existing app using .NET (no Node dependency).
try {
    $taskPackage=Get-AppxPackage 'OpenAI.Codex' -ErrorAction Stop | Select-Object -First 1
    [xml]$taskManifest=Get-Content -LiteralPath (Join-Path $taskPackage.InstallLocation 'AppxManifest.xml') -Raw -ErrorAction Stop
    $taskApplication=@($taskManifest.Package.Applications.Application | Where-Object {$_.Id -eq 'App'})
    if ($taskApplication.Count -ne 1 -or !$taskApplication[0].Executable) { throw 'Registered application entry unavailable.' }
    $taskRoot=[IO.Path]::GetFullPath($taskPackage.InstallLocation).TrimEnd('\')+'\'
    $taskExe=[IO.Path]::GetFullPath((Join-Path $taskRoot ([string]$taskApplication[0].Executable)))
    if (!$taskExe.StartsWith($taskRoot,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($taskExe) -ne '.exe') { throw 'Invalid registered executable.' }
    $taskNames=@('ChatGPT.exe','codex.exe',[IO.Path]::GetFileName($taskExe)) | Select-Object -Unique
    $taskFilter=($taskNames | ForEach-Object {"Name='"+$_.Replace("'","''")+"'"}) -join ' OR '
    $taskFamily=$taskPackage.PackageFamilyName -split '_',2
    $taskPackagePattern='\\WindowsApps\\'+[regex]::Escape($taskFamily[0])+'_[^\\]+_'+[regex]::Escape($taskFamily[1])+'\\app\\(?:'+(($taskNames | ForEach-Object {[regex]::Escape($_)}) -join '|')+')$'
    foreach ($taskOwner in @(Get-CimInstance Win32_Process -Filter $taskFilter -ErrorAction Stop)) {
        # WMI may report 8.3 short paths (e.g. RUNNER~1); normalize before comparing
        # with the registered executable, or unrelated-looking paths skip cleanup.
        $taskOwnerPath=$taskOwner.ExecutablePath
        if ($taskOwnerPath) { $taskOwnerPath=[IO.Path]::GetFullPath($taskOwnerPath) }
        if (($taskOwnerPath -ne $taskExe -and $taskOwnerPath -notmatch $taskPackagePattern) -or $taskOwner.CommandLine -match '(?:^|\s)--type(?:=|\s)') { continue }
        if ($taskOwner.CommandLine -notmatch '(?:^|\s)--remote-debugging-port(?:=|\s+)([0-9]+)(?:\s|$)') { continue }
        $taskPort=[int]$Matches[1]
        if ($taskPort -lt 1024 -or $taskPort -gt 65535) { continue }
        $taskListener=Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $taskOwner.ProcessId -and $_.LocalAddress -in @('127.0.0.1','::1') } | Select-Object -First 1
        if (!$taskListener) { continue }
        $taskTargets=Invoke-RestMethod "http://127.0.0.1:$taskPort/json/list" -TimeoutSec 2 -ErrorAction Stop
        foreach ($taskTarget in @($taskTargets | Where-Object { $_.type -eq 'page' -and $_.url -like 'app://*' } | Select-Object -First 8)) {
            $taskSocket=$null
            $taskCancel=$null
            try {
                $taskUri=[Uri]$taskTarget.webSocketDebuggerUrl
                if ($taskUri.Scheme -ne 'ws' -or $taskUri.Host -notin @('127.0.0.1','localhost') -or $taskUri.Port -ne $taskPort) { continue }
                $taskSocket=[Net.WebSockets.ClientWebSocket]::new()
                $taskCancel=[Threading.CancellationTokenSource]::new(2000)
                $taskSocket.ConnectAsync($taskUri,$taskCancel.Token).GetAwaiter().GetResult()
                $taskMessage=@{id=1;method='Runtime.evaluate';params=@{expression='document.getElementById("codex-sunfaded-local-theme")?.remove();document.getElementById("codex-sidebar-palette-filter")?.remove();'}} | ConvertTo-Json -Depth 4 -Compress
                $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskMessage)
                $taskSocket.SendAsync([ArraySegment[byte]]::new($taskBytes),[Net.WebSockets.WebSocketMessageType]::Text,$true,$taskCancel.Token).GetAwaiter().GetResult()
                $taskBuffer=[byte[]]::new(4096)
                $null=$taskSocket.ReceiveAsync([ArraySegment[byte]]::new($taskBuffer),$taskCancel.Token).GetAwaiter().GetResult()
            } catch { } finally { if ($taskSocket) { $taskSocket.Dispose() }; if ($taskCancel) { $taskCancel.Dispose() } }
        }
    }
} catch { }
if (!$NoLaunch) { Start-Process -FilePath 'explorer.exe' -ArgumentList 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App' -WindowStyle Hidden -ErrorAction Stop | Out-Null }
