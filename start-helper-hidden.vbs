Option Explicit

Dim shell, fso, scriptDir, helperPath
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
helperPath = scriptDir & "\OmniFetchHelper.exe"

If Not fso.FileExists(helperPath) Then
    MsgBox "OmniFetchHelper.exe not found: " & helperPath, 16, "OmniFetch"
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDir
shell.Run Chr(34) & helperPath & Chr(34), 0, False
