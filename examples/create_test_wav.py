#!/usr/bin/env python3
"""
Create a test WAV file with synthesized speech for testing Vosk STT.
Uses the system's text-to-speech if available, or generates a tone.
"""
import subprocess
import sys
from pathlib import Path


def create_test_wav_mac(output_path: str, text: str = "Hello, this is a test of the voice recognition system."):
    """Create test WAV using macOS 'say' command."""
    try:
        print(f"Creating test WAV file using macOS text-to-speech...")
        print(f"  Text: {text}")
        print(f"  Output: {output_path}")

        # Use macOS 'say' command to generate audio
        # -o outputs to file, --file-format=WAVE --data-format=LEI16@16000 sets format
        subprocess.run([
            "say",
            "-o", output_path,
            "--file-format=WAVE",
            "--data-format=LEI16@16000",
            "-v", "Samantha",  # Use Samantha voice
            text
        ], check=True)

        print(f"✓ Test WAV file created: {output_path}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"✗ Error running 'say' command: {e}")
        return False
    except FileNotFoundError:
        print("✗ 'say' command not found (not on macOS?)")
        return False


def create_test_wav_tone(output_path: str):
    """Create a simple tone WAV file as fallback."""
    import wave
    import struct
    import math

    print(f"Creating test WAV file with simple tone...")
    print(f"  Output: {output_path}")

    sample_rate = 16000
    duration = 2.0  # seconds
    frequency = 440.0  # A4 note

    num_samples = int(sample_rate * duration)

    with wave.open(output_path, 'w') as wav:
        wav.setnchannels(1)  # Mono
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)

        for i in range(num_samples):
            # Generate sine wave
            sample = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * i / sample_rate))
            wav.writeframes(struct.pack('<h', sample))

    print(f"✓ Test WAV file created: {output_path}")
    print("  Note: This is just a tone, not speech. Vosk won't transcribe it.")
    print("  To test with real speech, record yourself or use text-to-speech.")
    return True


def main():
    """Main entry point."""
    output_path = "test_audio.wav"

    if len(sys.argv) > 1:
        output_path = sys.argv[1]

    print("=" * 60)
    print("Creating Test WAV File")
    print("=" * 60)
    print()

    # Try macOS text-to-speech first
    if sys.platform == "darwin":
        if create_test_wav_mac(output_path):
            print()
            print("Now run:")
            print(f"  python examples/test_vosk_simple.py {output_path}")
            return 0

    # Fallback to tone
    print("Using fallback method (tone generation)...")
    if create_test_wav_tone(output_path):
        print()
        print("Now run:")
        print(f"  python examples/test_vosk_simple.py {output_path}")
        print()
        print("Or to test with real speech, record yourself saying something:")
        print("  On macOS: python examples/create_test_wav.py")
        print("  With ffmpeg: ffmpeg -f avfoundation -i \":0\" -t 5 -ac 1 -ar 16000 test_audio.wav")
        return 0

    print("✗ Failed to create test WAV file")
    return 1


if __name__ == "__main__":
    sys.exit(main())
