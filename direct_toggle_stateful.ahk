; Direct Voicepipe Toggle - AutoHotkey v2
; Stateful version that checks actual daemon status

global lastToggleTime := 0

; Alt+F5 = Toggle Recording (stateful with debouncing)
!F5::
{
    global lastToggleTime
    
    ; Get current time in milliseconds
    currentTime := A_TickCount
    
    ; Ignore if triggered within 500ms of last toggle
    if (currentTime - lastToggleTime < 500) {
        return
    }
    
    lastToggleTime := currentTime
    
    pythonwPath := "C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12\Scripts\pythonw.exe"
    controlScript := "C:\Users\fenlo\Documents\voicepipe\fast_control.py"
    
    ; Always toggle based on current daemon state
    ; The fast_control.py script already checks the actual state
    Run '"' . pythonwPath . '" "' . controlScript . '" toggle', , "Hide"
}

; Emergency stop - Alt+F12
!F12::
{
    Run 'powershell.exe -Command "Get-Process python*,ffmpeg* | Stop-Process -Force"', , "Hide"
}