"""Fast control script with minimal overhead"""
import sys
import json
import os

if sys.platform == "win32":
    import win32pipe
    import win32file
    
    def send_cmd(cmd):
        pipe = win32file.CreateFile(
            r'\\.\pipe\voicepipe_daemon',
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None, win32file.OPEN_EXISTING, 0, None
        )
        
        win32file.WriteFile(pipe, json.dumps({"command": cmd}).encode())
        hr, response = win32file.ReadFile(pipe, 4096)
        result = json.loads(response.decode())
        win32file.CloseHandle(pipe)
        return result
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        
        if cmd == "start":
            # Check if already recording
            status = send_cmd("status")
            if status and status.get('status') == 'recording':
                # Already recording, do nothing
                sys.exit(0)
            
            result = send_cmd("start")
            if 'error' not in result:
                print("Recording started")
                
        elif cmd == "toggle":
            # Get current status and toggle
            status = send_cmd("status")
            if status and status.get('status') == 'recording':
                # Currently recording, stop it
                result = send_cmd("stop")
                
                # Log to file for debugging
                with open(os.path.join(os.environ['TEMP'], 'voicepipe_debug.log'), 'a') as f:
                    f.write(f"\nToggle stop result: {result}\n")
                    
                    if 'audio_file' in result:
                        audio_file = result['audio_file']
                        f.write(f"Audio file: {audio_file}\n")
                        
                        if os.path.exists(audio_file):
                            size = os.path.getsize(audio_file)
                            f.write(f"File size: {size} bytes\n")
                            
                            if size > 0:
                                try:
                                    f.write("Importing modules...\n")
                                    from voicepipe.transcriber import WhisperTranscriber
                                    f.write("Transcriber imported\n")
                                    
                                    f.write("Creating transcriber...\n")
                                    transcriber = WhisperTranscriber()
                                    f.write("Transcriber created\n")
                                    
                                    f.write("Starting transcription...\n")
                                    text = transcriber.transcribe(audio_file)
                                    f.write(f"Transcription result: {text}\n")
                                    
                                    if text:
                                        f.write("Starting typing...\n")
                                        if sys.platform == "win32":
                                            try:
                                                import pyautogui
                                                pyautogui.typewrite(text, interval=0.01)
                                                f.write("Typing completed with pyautogui\n")
                                            except ImportError:
                                                f.write("pyautogui not installed\n")
                                            except Exception as e:
                                                f.write(f"Typing error: {e}\n")
                                    else:
                                        f.write("No text to type\n")
                                except Exception as e:
                                    f.write(f"Error: {type(e).__name__}: {e}\n")
                                    import traceback
                                    f.write(traceback.format_exc())
                            else:
                                f.write("Audio file is empty\n")
                        else:
                            f.write("Audio file does not exist\n")
                    else:
                        f.write("No audio_file in result\n")
            else:
                # Not recording, start it
                result = send_cmd("start")
                if 'error' not in result:
                    print("Recording started")
                    
        elif cmd == "stop":
            # Check if actually recording
            status = send_cmd("status")
            if not status or status.get('status') != 'recording':
                # Not recording, do nothing
                sys.exit(0)
                
            result = send_cmd("stop")
            
            # Log to file for debugging
            with open(os.path.join(os.environ['TEMP'], 'voicepipe_debug.log'), 'a') as f:
                f.write(f"\nStop result: {result}\n")
                
                if 'audio_file' in result:
                    audio_file = result['audio_file']
                    f.write(f"Audio file: {audio_file}\n")
                    
                    if os.path.exists(audio_file):
                        size = os.path.getsize(audio_file)
                        f.write(f"File size: {size} bytes\n")
                        
                        if size > 0:
                            try:
                                f.write("Importing modules...\n")
                                from voicepipe.transcriber import WhisperTranscriber
                                f.write("Transcriber imported\n")
                                
                                f.write("Creating transcriber...\n")
                                transcriber = WhisperTranscriber()
                                f.write("Transcriber created\n")
                                
                                f.write("Starting transcription...\n")
                                text = transcriber.transcribe(audio_file)
                                f.write(f"Transcription result: {text}\n")
                                
                                if text:
                                    f.write("Starting typing...\n")
                                    if sys.platform == "win32":
                                        try:
                                            import pyautogui
                                            pyautogui.typewrite(text, interval=0.01)
                                            f.write("Typing completed with pyautogui\n")
                                        except ImportError:
                                            f.write("pyautogui not installed\n")
                                        except Exception as e:
                                            f.write(f"Typing error: {e}\n")
                                else:
                                    f.write("No text to type\n")
                            except ImportError as e:
                                f.write(f"Import error: {e}\n")
                                import traceback
                                f.write(traceback.format_exc())
                            except Exception as e:
                                f.write(f"Error: {type(e).__name__}: {e}\n")
                                import traceback
                                f.write(traceback.format_exc())
                        else:
                            f.write("Audio file is empty\n")
                    else:
                        f.write("Audio file does not exist\n")
                else:
                    f.write("No audio_file in result\n")