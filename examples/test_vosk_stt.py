#!/usr/bin/env python3
"""
Test script to verify Vosk STT works correctly.
Transcribes a WAV file and returns the text.
"""
import asyncio
import sys
import wave
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_playground.local_agent import LocalVoskSTT
from livekit.agents import utils


async def transcribe_wav(wav_path: str) -> str:
    """Transcribe a WAV file using Vosk STT."""
    print(f"Loading WAV file: {wav_path}")

    # Open WAV file
    with wave.open(wav_path, 'rb') as wav:
        sample_rate = wav.getframerate()
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        n_frames = wav.getnframes()

        print(f"  Sample rate: {sample_rate} Hz")
        print(f"  Channels: {n_channels}")
        print(f"  Sample width: {sample_width} bytes")
        print(f"  Duration: {n_frames / sample_rate:.2f} seconds")

        # Read audio data
        audio_data = wav.readframes(n_frames)

    # Initialize Vosk STT
    print("\nInitializing Vosk STT...")
    stt = LocalVoskSTT(
        model_name="vosk-model-small-en-us-0.15",
        sample_rate=sample_rate,
        language="en",
    )

    # Create audio buffer
    print("Creating audio buffer...")
    audio_buffer = utils.AudioBuffer()

    # Convert to proper format if needed
    if n_channels == 2:
        print("Converting stereo to mono...")
        # Simple stereo to mono conversion - average the channels
        import struct
        frames = []
        for i in range(0, len(audio_data), sample_width * 2):
            left = struct.unpack('<h', audio_data[i:i+2])[0]
            right = struct.unpack('<h', audio_data[i+2:i+4])[0]
            mono = (left + right) // 2
            frames.append(struct.pack('<h', mono))
        audio_data = b''.join(frames)

    # Add audio to buffer
    from livekit import rtc
    audio_frame = rtc.AudioFrame(
        data=audio_data,
        sample_rate=sample_rate,
        num_channels=1,
        samples_per_channel=len(audio_data) // 2,  # 16-bit = 2 bytes per sample
    )
    audio_buffer.push(audio_frame)

    # Transcribe
    print("Transcribing...")
    try:
        # Use the streaming API
        stream = stt.stream(language="en")

        # Push audio data
        transcription_parts = []

        async with stream:
            # Feed the audio
            await stream.push_audio(audio_buffer.remix_and_resample(
                sample_rate=sample_rate,
                num_channels=1,
            ))

            # Flush to get final result
            await stream.flush()

            # Collect results
            async for event in stream:
                if event.type in ("FINAL_TRANSCRIPT", "END_OF_SPEECH"):
                    if event.alternatives and event.alternatives[0].text:
                        transcription_parts.append(event.alternatives[0].text)

        transcription = " ".join(transcription_parts).strip()
        return transcription

    except Exception as e:
        print(f"Error during transcription: {e}")
        import traceback
        traceback.print_exc()
        raise


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python test_vosk_stt.py <wav_file>")
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
        transcription = await transcribe_wav(wav_path)

        print()
        print("=" * 60)
        print("Transcription Result")
        print("=" * 60)
        print(transcription)
        print()

        if transcription:
            print("✓ SUCCESS: Transcription completed")
            return 0
        else:
            print("✗ FAILED: No transcription returned")
            return 1

    except Exception as e:
        print()
        print("=" * 60)
        print("Error")
        print("=" * 60)
        print(f"✗ FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
