Option Explicit
Dim shell, fso, folder, suffix, result, cmd, login, pet, watchFile, watchStep, watchPrefix, watchStream, watchLogin
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
If WScript.Arguments.Count > 1 Then
    If WScript.Arguments(0) = "watch" Then
        watchFile = WScript.Arguments(1)
        watchLogin = False
        If WScript.Arguments.Count > 2 Then
            If WScript.Arguments(2) = "login" Then watchLogin = True
        End If
        watchPrefix = fso.GetSpecialFolder(2) & "\CodexGuard-"
        If LCase(Left(watchFile, Len(watchPrefix))) <> LCase(watchPrefix) Then WScript.Quit 1
        If fso.GetParentFolderName(watchFile) <> CStr(fso.GetSpecialFolder(2)) Then WScript.Quit 1
        On Error Resume Next
        For watchStep = 1 To 40
            If fso.FileExists(watchFile) Then
                fso.DeleteFile watchFile, True
                WScript.Quit 0
            End If
            WScript.Sleep 500
        Next
        ' A hung theme wrapper must not block the original app entry forever.
        shell.Run "explorer.exe shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App", 0, False
        StartIntegratedPet watchLogin
        WScript.Quit 0
    End If
End If
folder = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\CodexSunFaded")
suffix = ""
login = False
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments(0) = "login" Then
        suffix = " login"
        login = True
    End If
    If WScript.Arguments(0) = "plan" Then
        WScript.Echo "Theme entry: " & folder & "\launch.vbs; fallback: Windows AppsFolder"
        WScript.Quit 0
    End If
End If
On Error Resume Next
watchFile = fso.GetSpecialFolder(2) & "\CodexGuard-" & fso.GetTempName & ".done"
cmd = "wscript.exe """ & WScript.ScriptFullName & """ watch """ & watchFile & """"
If login Then cmd = cmd & " login"
shell.Run cmd, 0, False
Err.Clear
result = 1
If fso.FileExists(folder & "\launch.vbs") Then
    cmd = "cscript.exe //B //Nologo """ & folder & "\launch.vbs""" & suffix
    result = shell.Run(cmd, 0, True)
End If
Set watchStream = fso.CreateTextFile(watchFile, True)
watchStream.Close
If Err.Number <> 0 Or result <> 0 Then
    Err.Clear
    shell.Run "explorer.exe shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App", 0, False
    StartIntegratedPet login
End If

Sub StartIntegratedPet(isLogin)
    Dim guardMarker, legacyMarker, executable
    If Not isLogin Then Exit Sub
    If Not PetLoginAllowed() Then Exit Sub
    guardMarker = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\CodexNativeGuard\.pet-integration")
    legacyMarker = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\CodexSunFaded\.pet-integration")
    If fso.FileExists(guardMarker) Or fso.FileExists(legacyMarker) Then
        executable = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\CodexToolsPet\CodexToolsPet.exe")
        If fso.FileExists(executable) Then shell.Run """" & executable & """", 0, False
    End If
End Sub

Function PetLoginAllowed()
    Dim preference
    PetLoginAllowed = True
    On Error Resume Next
    Err.Clear
    preference = shell.RegRead("HKCU\Software\CodexToolsPet\LoginEnabled")
    If Err.Number = 0 Then
        If VarType(preference) = 3 Then
            If preference = 0 Then PetLoginAllowed = False
        End If
    End If
    Err.Clear
End Function
