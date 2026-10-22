param([ValidateSet('pet','wallpaper')][string]$Mode='pet')
$ErrorActionPreference='Stop'
$env:PSModulePath=Join-Path $PSHOME 'Modules'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPet=Join-Path $taskRoot 'components\desktop-pet'
function Pick-Image([string]$Title,[string]$Filter) {
    Add-Type -AssemblyName System.Windows.Forms
    $taskDialog=New-Object System.Windows.Forms.OpenFileDialog
    $taskDialog.Title=$Title
    $taskDialog.Filter=$Filter
    try { if($taskDialog.ShowDialog() -ne 'OK'){ throw 'Image selection cancelled; no installation performed.' }; return $taskDialog.FileName } finally { $taskDialog.Dispose() }
}
function Invoke-Checked([string]$Program,[string[]]$Arguments) {
    & $Program @Arguments
    if($LASTEXITCODE -ne 0){throw "Command failed with exit code $LASTEXITCODE"}
}
if($Mode -eq 'pet') {
    # Qt ships deeply nested files. Keep dependencies out of long checkout paths.
    $taskRootIdentity=[IO.Path]::GetFullPath($taskRoot).TrimEnd([char[]]'\/').ToLowerInvariant()
    $taskHasher=[Security.Cryptography.SHA256]::Create()
    try { $taskEnvKey=([BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRootIdentity)))).Replace('-','').Substring(0,12).ToLowerInvariant() } finally { $taskHasher.Dispose() }
    $taskUserProfile=$env:USERPROFILE
    if(!$taskUserProfile){$taskUserProfile=[Environment]::GetFolderPath('UserProfile')}
    $taskVenv=Join-Path (Join-Path $taskUserProfile '.codex-tools\v') $taskEnvKey
    $taskPython=Join-Path $taskVenv 'Scripts\python.exe'
    if(!(Test-Path -LiteralPath $taskPython)) {
        $taskLauncher=(Get-Command py -ErrorAction SilentlyContinue).Source
        if(!$taskLauncher){throw 'Install Python 3.14 for Windows from python.org, including the Python launcher, then retry.'}
        Invoke-Checked $taskLauncher @('-3.14','-m','venv',$taskVenv)
    }
    $taskLock=Join-Path $taskPet 'requirements.lock.txt'
    $taskStamp=Join-Path $taskVenv 'requirements.sha256'
    $taskHash=(Get-FileHash -LiteralPath $taskLock -Algorithm SHA256).Hash
    if(!(Test-Path -LiteralPath $taskStamp) -or ([IO.File]::ReadAllText($taskStamp).Trim() -ne $taskHash)) {
        Invoke-Checked $taskPython @('-m','pip','install','-r',$taskLock)
        [IO.File]::WriteAllText($taskStamp,$taskHash)
    }
    $taskPack=Join-Path $taskPet 'user-assets\pet\pack.json'
    if(!(Test-Path -LiteralPath $taskPack)) {
        $taskImage=Pick-Image 'Choose your own transparent pet PNG' 'PNG images (*.png)|*.png'
        Invoke-Checked $taskPython @((Join-Path $taskPet 'tools\asset_pack.py'),'init','--image',$taskImage)
    }
    Invoke-Checked $taskPython @((Join-Path $taskPet 'tools\asset_pack.py'),'validate',$taskPack)
    Start-Process -FilePath (Join-Path $taskVenv 'Scripts\pythonw.exe') -ArgumentList ('"'+(Join-Path $taskPet 'run_pet.py')+'"') -WorkingDirectory $taskPet -WindowStyle Hidden
} else {
    $taskPython=(Get-Command py -ErrorAction SilentlyContinue).Source
    $taskPrefix=@('-3')
    if(!$taskPython){$taskPython=(Get-Command python -ErrorAction SilentlyContinue).Source;$taskPrefix=@()}
    if(!$taskPython){throw 'Install Python 3 from python.org before building wallpaper resources.'}
    $taskBackground=Pick-Image 'Choose your own main background PNG' 'PNG images (*.png)|*.png'
    $taskRepeat=Pick-Image 'Choose your repeating sidebar JPEG' 'JPEG images (*.jpg;*.jpeg)|*.jpg;*.jpeg'
    $taskEnding=Pick-Image 'Choose your bottom sidebar JPEG' 'JPEG images (*.jpg;*.jpeg)|*.jpg;*.jpeg'
    $taskSource=Join-Path $taskRoot 'components\codex-wallpaper\源码'
    $taskOutput=Join-Path $taskRoot 'components\codex-wallpaper\build\own-images'
    Invoke-Checked $taskPython ($taskPrefix+@((Join-Path $taskSource 'prepare_images.py'),'--background',$taskBackground,'--repeat',$taskRepeat,'--ending',$taskEnding,'--output',$taskOutput))
    & (Join-Path $taskSource 'Install-FixedConfig.ps1') -AssetDirectory $taskOutput
    Write-Host 'Installation complete. Fully exit Codex, then open it with the desktop shortcut.'
}
