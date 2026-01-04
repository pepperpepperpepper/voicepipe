; Voicepipe hotkey example (AutoHotkey v1)
;
; Win+Shift+V toggles Voicepipe:
; - first press: start recording
; - second press: stop + transcribe (and optionally type)
;
; The "Hide" flag avoids a visible console window when triggering the command.

# + v::
    Run, voicepipe-fast toggle, , Hide
return

