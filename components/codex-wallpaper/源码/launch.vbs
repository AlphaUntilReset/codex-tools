Option Explicit
Dim shell, fso, folder, cmd, suffix, result, login, pet
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
suffix = ""
login = False
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments(0) = "login" Then
        suffix = " -Login"
        login = True
    End If
End If
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & folder & "\Fixed-Launcher.ps1""" & suffix
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments(0) = "plan" Then
        WScript.Echo cmd
        WScript.Quit 0
    End If
End If
On Error Resume Next
result = 1
If fso.FileExists(folder & "\Fixed-Launcher.ps1") Then result = shell.Run(cmd, 0, True)
If Err.Number <> 0 Or result <> 0 Then
    Err.Clear
    shell.Run "explorer.exe shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App", 0, False
    If login And fso.FileExists(folder & "\.pet-integration") And PetLoginAllowed() Then
        pet = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\CodexToolsPet\CodexToolsPet.exe")
        If fso.FileExists(pet) Then shell.Run """" & pet & """", 0, False
    End If
End If

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
