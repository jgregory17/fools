#!/usr/bin/env python3
"""
Simple test to verify Vosk STT works correctly.
Transcribes a WAV file using Vosk directly.
"""
import json
import sys
import wave
from pathlib import Path

try:
    from vosk import Model, KaldiRecognizer
except ImportError:
    print("Error: vosk not installed. Run: pip install vosk")
    sys.exit(1)


def transcribe_wav(wav_path: str) -> str:
    """Transcribe a WAV file using Vosk."""
    print(f"Loading WAV file: {wav_path}")

    # Open WAV file
    with wave.open(wav_path, 'rb') as wav:
        sample_rate = wav.getframerate()
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        n_frames = wav.getnframes()
        duration = n_frames / sample_rate

        print(f"  Sample rate: {sample_rate} Hz")
        print(f"  Channels: {n_channels}")
        print(f"  Sample width: {sample_width} bytes")
        print(f"  Duration: {duration:.2f} seconds")

        # Vosk requires mono, 16-bit PCM
        if n_channels != 1:
            print("Error: WAV file must be mono (1 channel)")
            print("Convert with: ffmpeg -i input.wav -ac 1 -ar 16000 output.wav")
            return ""

        if sample_rate not in [8000, 16000, 32000, 48000]:
            print(f"Warning: Sample rate {sample_rate} Hz may not work well")
            print("Recommended: 16000 Hz")

        # Initialize Vosk model
        print("\nInitializing Vosk model...")
        model_path = Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"

        if not model_path.exists():
            print(f"Error: Model not found at {model_path}")
            print("The model should be automatically downloaded on first use")
            print("Try running the full agent first to download the model")
            return ""

        model = Model(str(model_path))
        recognizer = KaldiRecognizer(model, sample_rate)
        recognizer.SetWords(True)

        # Process audio in chunks
        print("Transcribing...")
        chunk_size = 4000  # Process 4000 bytes at a time

        while True:
            data = wav.readframes(chunk_size)
            if len(data) == 0:
                break

            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                if text:
                    print(f"  Partial: {text}")

        # Get final result
        final_result = json.loads(recognizer.FinalResult())
        final_text = final_result.get("text", "")

        return final_text


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python test_vosk_simple.py <wav_file>")
        print()
        print("The WAV file must be:")
        print("  - Mono (1 channel)")
        print("  - 16-bit PCM")
        print("  - Recommended sample rate: 16000 Hz")
        print()
        print("To create a test WAV file from any audio:")
        print("  ffmpeg -i input.mp3 -ac 1 -ar 16000 -sample_fmt s16 test.wav")
        sys.exit(1)

    wav_path = sys.argv[1]

    if not Path(wav_path).exists():
        print(f"Error: File not found: {wav_path}")
        sys.exit(1)

    print("=" * 60)
    print("Testing Vosk STT")
    print("=" * 60)
    print()

    try:
        transcription = transcribe_wav(wav_path)

        print()
        print("=" * 60)
        print("Transcription Result")
        print("=" * 60)
        if transcription:
            print(transcription)
            print()
            print("✓ SUCCESS: Vosk STT is working correctly")
            return 0
        else:
            print("(no transcription)")
            print()
            print("✗ FAILED: No transcription returned")
            print("This could mean:")
            print("  - The audio is silent")
            print("  - The audio format is incorrect")
            print("  - The model couldn't recognize the speech")
            return 1

    except Exception as e:
        print()
        print("=" * 60)
        print("Error")
        print("=" * 60)
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
