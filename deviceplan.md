Device auto-detection plan
===========================

Goal
----
Make microphone selection automatic by default, with a safe fallback and a
simple override. Avoid requiring users to know device names/indices.

Key observations (current Linux pain)
-------------------------------------
- PipeWire/Pulse "pulse" device can return silence even when the mic is live.
- ALSA hardware device (e.g., hw:0,6) captures correctly but needs the right
  sample format/rate (often S32_LE, 48k, 2ch).
- PortAudio device indices are unstable across boots and differ from ALSA
  card/device IDs.

Principles
----------
- Prefer "default" input when it actually produces non-zero audio.
- Always probe a candidate device with a short read to confirm real signal.
- Never block startup; detection runs fast (<= ~1s) and caches result.
- Provide a one-command wizard to override when auto-selection fails.

Config sources (priority order)
-------------------------------
1) Explicit user config (VOICEPIPE_DEVICE / voicepipe.env / ~/.config/voicepipe/device)
2) Auto-detected cached result (new: ~/.config/voicepipe/device_cache.json)
3) Auto-detection (fresh probe)

Auto-detection algorithm (Linux)
--------------------------------
Inputs:
- PortAudio device list (sounddevice)
- PulseAudio sources (pactl) if available

Steps:
1) If user-configured device exists: use it (do not auto-select).
2) If cached selection exists and still works: use it.
3) Build candidate list:
   - sounddevice default input
   - "pulse" device
   - "pipewire" device
   - "default" device
   - all input devices (by index)
4) For each candidate:
   - Probe audio with a short capture (0.3s) using supported rates/channels
   - If max amplitude > threshold (e.g., 50 for int16), accept
5) If all candidates fail or are silent:
   - Fallback to ALSA hardware list if available (arecord -l) and try
     matching PortAudio device names to ALSA entries (best-effort)
6) Cache the first working device (index + name + sample rate + channels).

Device probing behavior
-----------------------
- Try a short capture (0.3–0.5s).
- Test a small set of (rate, channels) combos:
  - Preferred: 48000/2, 48000/1, 16000/1, 44100/2, 44100/1
- Consider "silent" if max amplitude <= threshold.

Cache format (new file)
-----------------------
Path: ~/.config/voicepipe/device_cache.json
Example:
{
  "device_index": 4,
  "device_name": "sof-hda-dsp: - (hw:0,6)",
  "samplerate": 48000,
  "channels": 2,
  "source": "auto",
  "last_ok": "2026-01-04T02:00:00Z"
}

Wizard (manual override)
------------------------
Command: voicepipe config audio
- Lists detected inputs and probes them (short recording).
- Shows levels, defaults to loudest device.
- Writes config (VOICEPIPE_DEVICE) and updates cache.

Daemon / CLI behavior
---------------------
- On daemon startup: use config -> cache -> auto-detect.
- On each recording start: reuse selected device unless a manual override is
  passed; optionally re-validate if silence detected.
- Log the selected device and whether it was configured, cached, or auto.

Failure modes and fallbacks
---------------------------
- If probe reads all-zero data:
  - Retry once with different sample rate/channels.
  - If still silent: move to next candidate.
- If no device works:
  - Use default device and emit a warning with "voicepipe config audio".

Non-Linux platforms
-------------------
- macOS/Windows: keep existing PortAudio default selection; optionally add
  probing logic but skip Pulse/ALSA specifics.

Acceptance criteria
-------------------
- Fresh install on Linux records from the real mic without manual config.
- If Pulse is silent, it automatically falls back to a working ALSA device.
- `voicepipe config audio` reliably finds the loudest mic and writes config.
