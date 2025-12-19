# Audio Pipeline Debugging Status

## What's Working ✅

### STT (Vosk) - Standalone
- Vosk library installed and functional
- Model loaded successfully (vosk-model-small-en-us-0.15)
- Transcribes macOS `say` generated audio perfectly
- Round-trip test: "hello this is a test" → transcribed correctly

### TTS (Piper) - Standalone
- Piper installed and functional
- Voice model available (en_US-lessac-medium)
- Generated 126KB WAV file from text
- Round-trip: TTS output → Vosk STT transcribed correctly

### LiveKit Integration Fixes Applied
- ✅ Fixed `VoskSpeechStream.__init__()` - added `stt_instance` parameter
- ✅ Fixed `VoskSpeechStream.stream()` - added `conn_options` parameter
- ✅ Fixed `SpeechEvent` - changed `speech_id` to `request_id`
- ✅ Implemented `_run()` async method properly
- ✅ Agent registers with LiveKit successfully
- ✅ Agent accepts connection from browser

## What's Broken ❌

### End-to-End Audio Pipeline
**Symptoms:**
- User speaks into microphone → indicator shows "speaking"
- VAD detects speech (EOU metrics show detection)
- Vosk initializes without errors
- BUT: No transcriptions generated
- No LLM responses
- No TTS audio output
- Errors: "speech not done in time after interruption"

**Possible Causes:**
1. **Audio Format Mismatch**
   - Browser sends audio in format Vosk doesn't recognize
   - Sample rate mismatch (browser vs Vosk expects 16kHz)
   - Channel mismatch (stereo vs mono)

2. **Audio Routing Issue**
   - LiveKit not routing audio to VoskSpeechStream
   - `_run()` method not receiving audio frames
   - `_input_ch` not getting populated

3. **Vosk Recognition Failure**
   - Audio quality too low
   - Background noise
   - Microphone volume too low
   - Vosk model not suitable for user's voice

## Recent Connection Logs (03:13)

```
✓ Agent started successfully
✓ Vosk model loaded
✓ Agent entered session
✓ VAD detected speech (EOU metrics show delays of ~1s)
✗ No FINAL_TRANSCRIPT events
✗ No LLM responses generated
✗ Speech timeouts after 5s
```

## Next Steps to Debug

1. **Verify Audio is Reaching Vosk**
   - Added debug logging to `_run()` to log audio receipt
   - Need to rebuild and check if Vosk receives audio frames

2. **Check Audio Format**
   - Log sample rate from LiveKit audio frames
   - Verify 16kHz, mono, PCM16 format

3. **Test with Simple Audio**
   - Generate known-good audio and inject into pipeline
   - Verify Vosk can transcribe it

4. **Check Browser Console**
   - Look for WebRTC errors
   - Check if microphone permissions are correct
   - Verify audio is being sent from browser

## Test Commands

### Standalone Vosk Test
```bash
docker compose exec agent python -c "
import json, wave
from vosk import Model, KaldiRecognizer
from pathlib import Path

with wave.open('test_audio.wav', 'rb') as wav:
    model = Model(str(Path.home() / '.cache/vosk/vosk-model-small-en-us-0.15'))
    recognizer = KaldiRecognizer(model, wav.getframerate())
    while True:
        data = wav.readframes(4000)
        if not data: break
        recognizer.AcceptWaveform(data)
    print(json.loads(recognizer.FinalResult())['text'])
"
```

### Standalone Piper Test
```bash
docker compose exec agent bash -c "
echo 'Hello world' | piper \
  --model /home/agent/.local/share/piper/en_US-lessac-medium.onnx \
  --output_file /app/test.wav
"
```

### Round-Trip Test
```bash
# TTS → STT
docker cp test_audio.wav fools-agent-1:/app/
docker compose exec agent python examples/test_vosk_simple.py /app/test_audio.wav
```

## Files Modified

- `src/agent_playground/local_agent.py` - Fixed VoskSpeechStream integration
- `docs/FAKE_MODE_SETUP.md` - Updated with all fixes
- `examples/test_vosk_simple.py` - Created standalone STT test
- `examples/test_piper_simple.sh` - Created standalone TTS test
- `examples/create_test_wav.py` - Created test audio generator

## Status Summary

**STT + TTS Individually**: ✅ Working perfectly
**LiveKit Integration**: ❌ Audio not transcribing
**Root Cause**: Unknown - audio routing or format issue suspected
