#!/usr/bin/env python3
"""
Test script to verify Piper TTS works correctly.
Synthesizes text to speech and saves to WAV file.
"""
import subprocess
import sys
from pathlib import Path


def synthesize_piper(text: str, output_path: str, voice_model: str = "en_US-lessac-medium"):
    """Synthesize speech using Piper TTS."""
    print(f"Synthesizing with Piper TTS...")
    print(f"  Text: {text}")
    print(f"  Voice: {voice_model}")
    print(f"  Output: {output_path}")

    # Path to Piper binary and voice model
    piper_bin = Path.home() / ".local" / "bin" / "piper"
    voice_path = Path.home() / ".local" / "share" / "piper-voices" / f"{voice_model}.onnx"

    if not piper_bin.exists():
        print(f"Error: Piper binary not found at {piper_bin}")
        print("Install with: pip install piper-tts")
        return False

    if not voice_path.exists():
        print(f"Error: Voice model not found at {voice_path}")
        print(f"Download with: piper --model {voice_model} --download-dir ~/.local/share/piper-voices")
        return False

    try:
        # Run Piper to synthesize
        # piper --model <model> --output_file <wav> < text
        process = subprocess.Popen(
            [
                str(piper_bin),
                "--model", str(voice_path),
                "--output_file", output_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stdout, stderr = process.communicate(input=text)

        if process.returncode != 0:
            print(f"Error: Piper failed with code {process.returncode}")
            print(f"stderr: {stderr}")
            return False

        print(f"✓ Audio synthesized: {output_path}")
        return True

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    text = "Hello, this is a test of the text to speech system."
    output_path = "test_tts_output.wav"

    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    if len(sys.argv) > 2:
        output_path = sys.argv[2]

    print("=" * 60)
    print("Testing Piper TTS")
    print("=" * 60)
    print()

    if synthesize_piper(text, output_path):
        print()
        print("✓ SUCCESS: Piper TTS is working correctly")
        print()
        print("Now test with:")
        print(f"  python examples/test_vosk_simple.py {output_path}")
        return 0
    else:
        print()
        print("✗ FAILED: Piper TTS test failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
