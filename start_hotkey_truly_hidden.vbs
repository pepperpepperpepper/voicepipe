' Start AutoHotkey script completely hidden
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Change to the voicepipe directory
objShell.CurrentDirectory = "C:\Users\fenlo\Documents\voicepipe"

' Run the AutoHotkey script with window style 0 (hidden)
' Using the new hidden version of the script
objShell.Run "direct_toggle_hidden.ahk", 0, False