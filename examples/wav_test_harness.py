#!/usr/bin/env python3
"""
WAV File Test Harness - Process audio files through the voice pipeline.

Tests the complete voice pipeline with real audio files:
1. Load WAV file
2. Run VAD to detect speech segments
3. Transcribe with ASR
4. Generate LLM response
5. Synthesize TTS output
6. Optionally save output WAV

Requirements:
    pip install faster-whisper piper-tts httpx numpy torch

Usage:
    # Process a wav file:
    python examples/wav_test_harness.py input.wav

    # Save TTS output:
    python examples/wav_test_harness.py input.wav --output response.wav

    # Use custom models:
    python examples/wav_test_harness.py input.wav --asr-model small.en --llm-model mistral

    # Skip TTS synthesis:
    python examples/wav_test_harness.py input.wav --no-tts

    # Generate sample test file:
    python examples/wav_test_harness.py --generate-sample sample.wav
"""

import asyncio
import argparse
import logging
import struct
import sys
import wave
import math
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_playground.core.audio import CANONICAL_FORMAT
from agent_playground.modules.asr import FasterWhisperASR
from agent_playground.modules.llm import OllamaLLM
from agent_playground.modules.tts import PiperTTS
from agent_playground.modules.vad import SileroVAD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Results from processing a wav file."""
    input_file: str
    duration_ms: float
    vad_speech_frames: int
    vad_silence_frames: int
    transcript: str
    llm_response: str
    tts_audio_bytes: int
    tts_duration_ms: float


def load_wav(filepath: str) -> Tuple[bytes, int, int]:
    """
    Load a WAV file and return (audio_bytes, sample_rate, num_channels).

    Converts to PCM16 mono if necessary.
    """
    with wave.open(filepath, 'rb') as wf:
        sample_rate = wf.getframerate()
        num_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        num_frames = wf.getnframes()

        raw_data = wf.readframes(num_frames)

    logger.info(f"Loaded: {filepath}")
    logger.info(f"  Format: {sample_rate}Hz, {num_channels}ch, {sample_width * 8}bit")
    logger.info(f"  Duration: {num_frames / sample_rate * 1000:.0f}ms")

    # Convert to mono PCM16 if needed
    if sample_width == 1:
        # 8-bit to 16-bit
        samples = struct.unpack(f'{len(raw_data)}B', raw_data)
        samples = [(s - 128) * 256 for s in samples]
        raw_data = struct.pack(f'<{len(samples)}h', *samples)
        sample_width = 2
    elif sample_width == 4:
        # 32-bit to 16-bit
        samples = struct.unpack(f'<{len(raw_data) // 4}i', raw_data)
        samples = [s >> 16 for s in samples]
        raw_data = struct.pack(f'<{len(samples)}h', *samples)
        sample_width = 2

    # Convert to mono if stereo
    if num_channels == 2:
        samples = struct.unpack(f'<{len(raw_data) // 2}h', raw_data)
        mono_samples = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
        raw_data = struct.pack(f'<{len(mono_samples)}h', *mono_samples)
        num_channels = 1

    return raw_data, sample_rate, num_channels


def save_wav(filepath: str, audio_data: bytes, sample_rate: int = 24000):
    """Save audio data to a WAV file."""
    with wave.open(filepath, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data)

    duration_ms = len(audio_data) / 2 / sample_rate * 1000
    logger.info(f"Saved: {filepath} ({duration_ms:.0f}ms)")


def resample_audio(audio_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Simple linear interpolation resampling."""
    if from_rate == to_rate:
        return audio_data

    samples_in = struct.unpack(f'<{len(audio_data) // 2}h', audio_data)
    ratio = to_rate / from_rate
    num_out = int(len(samples_in) * ratio)

    samples_out = []
    for i in range(num_out):
        src_idx = i / ratio
        idx0 = int(src_idx)
        idx1 = min(idx0 + 1, len(samples_in) - 1)
        frac = src_idx - idx0

        sample = int(samples_in[idx0] * (1 - frac) + samples_in[idx1] * frac)
        samples_out.append(max(-32768, min(32767, sample)))

    return struct.pack(f'<{len(samples_out)}h', *samples_out)


def generate_sample_wav(filepath: str, duration_ms: int = 3000):
    """
    Generate a sample WAV file with synthesized "speech-like" audio.

    Creates a simple modulated tone that VAD can detect.
    """
    sample_rate = 24000
    num_samples = int(sample_rate * duration_ms / 1000)

    samples = []
    for i in range(num_samples):
        t = i / sample_rate

        # Create speech-like envelope
        if t < 0.3:
            # Silence at start
            amplitude = 0
        elif t < 2.5:
            # "Speech" period with AM modulation
            envelope = 0.5 + 0.5 * math.sin(2 * math.pi * 4 * t)  # 4Hz envelope
            amplitude = int(15000 * envelope)
        else:
            # Silence at end
            amplitude = 0

        # Fundamental + harmonics
        freq = 150 + 50 * math.sin(2 * math.pi * 0.5 * t)  # Varying pitch
        sample = int(amplitude * (
            0.6 * math.sin(2 * math.pi * freq * t) +
            0.3 * math.sin(2 * math.pi * freq * 2 * t) +
            0.1 * math.sin(2 * math.pi * freq * 3 * t)
        ))
        samples.append(max(-32768, min(32767, sample)))

    audio_data = struct.pack(f'<{num_samples}h', *samples)
    save_wav(filepath, audio_data, sample_rate)
    logger.info(f"Generated sample file: {filepath}")


async def process_wav(
    input_file: str,
    output_file: Optional[str] = None,
    asr_model: str = "base.en",
    llm_model: str = "llama3.2",
    system_prompt: str = "You are a helpful voice assistant. Be concise - respond in 1-2 sentences.",
    skip_tts: bool = False,
) -> PipelineResult:
    """Process a WAV file through the full voice pipeline."""

    # Load input
    audio_data, sample_rate, num_channels = load_wav(input_file)
    input_duration_ms = len(audio_data) / 2 / sample_rate * 1000

    # Resample to canonical format if needed
    if sample_rate != CANONICAL_FORMAT.sample_rate:
        logger.info(f"Resampling: {sample_rate}Hz -> {CANONICAL_FORMAT.sample_rate}Hz")
        audio_data = resample_audio(audio_data, sample_rate, CANONICAL_FORMAT.sample_rate)

    # Initialize components
    logger.info("\nInitializing pipeline components...")

    vad = SileroVAD()
    await vad.initialize()
    logger.info("  VAD: ready")

    asr = FasterWhisperASR(model_size=asr_model, device="auto")
    await asr.initialize()
    logger.info(f"  ASR: ready ({asr_model})")

    llm = OllamaLLM(model=llm_model, base_url="http://localhost:11434")
    await llm.initialize()
    health = await llm.health_check()
    if not health.get("available"):
        raise RuntimeError("Ollama not available. Start with: ollama serve")
    logger.info(f"  LLM: ready ({llm_model})")

    tts = None
    if not skip_tts:
        tts = PiperTTS(voice="en_US-lessac-medium")
        await tts.initialize()
        logger.info("  TTS: ready")

    # Step 1: VAD analysis
    logger.info("\n[1/4] Running VAD...")
    frame_size = CANONICAL_FORMAT.samples_per_frame * 2
    speech_frames = 0
    silence_frames = 0

    for i in range(0, len(audio_data), frame_size):
        frame = audio_data[i:i + frame_size]
        if len(frame) < frame_size:
            frame += b'\x00' * (frame_size - len(frame))

        is_speech = await vad.process_frame(frame)
        if is_speech:
            speech_frames += 1
        else:
            silence_frames += 1

    total_frames = speech_frames + silence_frames
    speech_pct = speech_frames / total_frames * 100 if total_frames > 0 else 0
    logger.info(f"  Speech: {speech_frames} frames ({speech_pct:.1f}%)")
    logger.info(f"  Silence: {silence_frames} frames")

    # Step 2: ASR transcription
    logger.info("\n[2/4] Running ASR...")
    transcript_parts = []
    async for result in asr.transcribe_stream(audio_data):
        if result.text.strip():
            transcript_parts.append(result.text.strip())
            logger.info(f"  Segment: {result.text.strip()}")

    transcript = " ".join(transcript_parts) if transcript_parts else "(no speech detected)"
    logger.info(f"\n  Full transcript: {transcript}")

    # Step 3: LLM response
    logger.info("\n[3/4] Generating LLM response...")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcript},
    ]

    llm_response = ""
    print("\n  Assistant: ", end="", flush=True)
    async for chunk in llm.generate_stream(messages):
        if chunk.text:
            print(chunk.text, end="", flush=True)
            llm_response += chunk.text
    print("\n")

    # Step 4: TTS synthesis
    tts_audio = b""
    tts_duration_ms = 0.0

    if tts and llm_response:
        logger.info("[4/4] Synthesizing TTS...")
        async for tts_chunk in tts.synthesize_stream(llm_response):
            tts_audio += tts_chunk.audio_data

        tts_duration_ms = len(tts_audio) / 2 / CANONICAL_FORMAT.sample_rate * 1000
        logger.info(f"  Generated: {len(tts_audio)} bytes ({tts_duration_ms:.0f}ms)")

        if output_file:
            save_wav(output_file, tts_audio, CANONICAL_FORMAT.sample_rate)
    else:
        logger.info("[4/4] TTS: skipped")

    # Cleanup
    await vad.close()
    await asr.close()
    await llm.close()
    if tts:
        await tts.close()

    return PipelineResult(
        input_file=input_file,
        duration_ms=input_duration_ms,
        vad_speech_frames=speech_frames,
        vad_silence_frames=silence_frames,
        transcript=transcript,
        llm_response=llm_response,
        tts_audio_bytes=len(tts_audio),
        tts_duration_ms=tts_duration_ms,
    )


async def main():
    parser = argparse.ArgumentParser(
        description="Process WAV files through the voice pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", nargs="?", help="Input WAV file path")
    parser.add_argument("--output", "-o", help="Output WAV file for TTS response")
    parser.add_argument("--asr-model", default="base.en", help="Whisper model size (default: base.en)")
    parser.add_argument("--llm-model", default="llama3.2", help="Ollama model (default: llama3.2)")
    parser.add_argument("--system-prompt", default="You are a helpful voice assistant. Be concise.",
                       help="System prompt for LLM")
    parser.add_argument("--no-tts", action="store_true", help="Skip TTS synthesis")
    parser.add_argument("--generate-sample", metavar="FILE", help="Generate a sample test WAV file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Generate sample file
    if args.generate_sample:
        generate_sample_wav(args.generate_sample)
        return 0

    # Require input file
    if not args.input:
        parser.print_help()
        print("\nError: input WAV file required (or use --generate-sample)")
        return 1

    if not Path(args.input).exists():
        print(f"Error: file not found: {args.input}")
        return 1

    print("\n" + "=" * 60)
    print("WAV File Test Harness")
    print("=" * 60)
    print(f"Input: {args.input}")
    print(f"ASR Model: {args.asr_model}")
    print(f"LLM Model: {args.llm_model}")
    print(f"Output: {args.output or '(none)'}")

    try:
        result = await process_wav(
            input_file=args.input,
            output_file=args.output,
            asr_model=args.asr_model,
            llm_model=args.llm_model,
            system_prompt=args.system_prompt,
            skip_tts=args.no_tts,
        )

        print("\n" + "=" * 60)
        print("Results Summary")
        print("=" * 60)
        print(f"Input Duration: {result.duration_ms:.0f}ms")
        print(f"VAD Speech: {result.vad_speech_frames} frames")
        print(f"Transcript: {result.transcript}")
        print(f"LLM Response: {result.llm_response[:100]}{'...' if len(result.llm_response) > 100 else ''}")
        if result.tts_audio_bytes > 0:
            print(f"TTS Output: {result.tts_duration_ms:.0f}ms")

        print("\n[OK] Pipeline completed successfully")
        return 0

    except FileNotFoundError as e:
        print(f"\n[FAIL] File not found: {e}")
        return 1
    except ImportError as e:
        print(f"\n[FAIL] Missing dependency: {e}")
        print("Install: pip install faster-whisper piper-tts httpx numpy torch")
        return 1
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        return 1
    except Exception as e:
        logger.exception("Pipeline error")
        print(f"\n[FAIL] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
