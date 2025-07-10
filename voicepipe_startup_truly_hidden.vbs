' Voicepipe Startup Script - Runs daemon and hotkeys completely hidden
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Wait a moment for Windows to fully start
WScript.Sleep 3000

' Start daemon with pythonw (no console window)
pythonwPath = "C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12\Scripts\pythonw.exe"
voicepipeScript = "C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12\Scripts\voicepipe"
daemonCmd = """" & pythonwPath & """ """ & voicepipeScript & """ daemon"

' Get OPENAI_API_KEY from system environment
Dim sysEnv
Set sysEnv = objShell.Environment("SYSTEM")
apiKey = sysEnv("OPENAI_API_KEY")

' If not in system env, try user env
If apiKey = "" Then
    Set userEnv = objShell.Environment("USER")
    apiKey = userEnv("OPENAI_API_KEY")
End If

' Pass the API key to the daemon process if found
If apiKey <> "" Then
    objShell.Environment("PROCESS")("OPENAI_API_KEY") = apiKey
End If

' Run daemon hidden
objShell.Run daemonCmd, 0, False

' Wait for daemon to initialize
WScript.Sleep 3000

' Start hotkeys using the truly hidden version
objShell.CurrentDirectory = "C:\Users\fenlo\Documents\voicepipe"
objShell.Run "wscript start_hotkey_truly_hidden.vbs", 0, False