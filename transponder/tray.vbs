' The tray with no window anywhere in the chain. wscript is a windowless host, and style 0
' hides the console that uv's venv pythonw -- a console-subsystem trampoline, checked against
' uv 0.11.26 -- would otherwise drag onto the screen for the user to close, killing the icon.
' Start it at login: a shortcut in shell:startup ->  wscript.exe //B "<path to this file>"
' Works from a checkout: it finds .venv two levels up from where it lives.
Dim fso, sh, root
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
sh.Run """" & fso.BuildPath(root, ".venv\Scripts\transponder-tray.exe") & """", 0, False
