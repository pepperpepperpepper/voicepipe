# Linux Performance TODO

## Issue: Linux recording startup is ~500ms vs Windows ~100ms

### Investigation needed:

1. **Profile Linux audio initialization**
   - Add timing logs to daemon's `_start_recording()` method
   - Time each step separately:
     - Audio device detection
     - sounddevice stream creation
     - ffmpeg process startup
   - Compare with Windows timings

2. **Check for Linux-specific bottlenecks**
   - PulseAudio vs ALSA initialization time
   - Default device enumeration overhead
   - File system operations (temp file creation)

3. **Potential optimizations to port from Windows branch**
   - Async ffmpeg startup (already in FastAudioRecorder but not AudioRecorder)
   - Pre-initialize audio stream pool
   - Cache default audio device info

4. **Test with different audio backends**
   - Try JACK for lower latency
   - Test with ALSA directly (bypass PulseAudio)
   - Check if `sd.default.latency = 'low'` helps

### Quick wins:
- Make AudioRecorder use async ffmpeg start like FastAudioRecorder does
- Pre-warm the audio system on daemon startup
- Consider keeping a persistent ffmpeg process pool

### Measurement script needed:
```python
# Add to daemon.py temporarily
import time
start_times = {}
start_times['total'] = time.perf_counter()
# ... before each major step:
start_times['device_init'] = time.perf_counter()
# ... after recording starts:
for step, t in start_times.items():
    print(f"{step}: {(time.perf_counter() - t) * 1000:.1f}ms")
```