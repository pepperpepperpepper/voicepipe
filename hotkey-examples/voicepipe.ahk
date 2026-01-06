; Voicepipe hotkey example (AutoHotkey v2)
;
; Win+Shift+V toggles Voicepipe:
; - first press: start recording
; - second press: stop + transcribe (and optionally type)
;
; The "Hide" flag avoids a visible console window when triggering the command.

#Requires AutoHotkey v2.0
#SingleInstance Force

#+v::
{
    Run "voicepipe-fast toggle", , "Hide"
}
