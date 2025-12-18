#!/usr/bin/env python3
"""
Voice Agent Demo - End-to-End with Real Backends.

Demonstrates the complete voice pipeline with real local inference:
- ASR: faster-whisper for transcription
- LLM: Ollama for generation
- TTS: Piper for speech synthesis
- VAD: Silero for voice activity detection

Requirements:
    pip install faster-whisper piper-tts httpx numpy torch

    # Start Ollama server:
    ollama serve
    ollama pull llama3.2

Usage:
    python examples/voice_agent_demo.py

    # With custom model:
    python examples/voice_agent_demo.py --llm-model mistral

    # Verbose output:
    python examples/voice_agent_demo.py --verbose
"""

import asyncio
import argparse
import logging
import sys
import struct
import math
from pathlib import Path
from typing import Optional

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_playground.core.event_bus import EventBus
from agent_playground.core.events import ASRResult, LLMChunk, TTSChunk, VADEvent
from agent_playground.core.audio import CANONICAL_FORMAT
from agent_playground.modules.asr import FasterWhisperASR
from agent_playground.modules.llm import OllamaLLM
from agent_playground.modules.tts import PiperTTS
from agent_playground.modules.vad import SileroVAD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def generate_test_audio(duration_ms: int = 2000, frequency: float = 440.0) -> bytes:
    """Generate a simple sine wave for testing audio pipeline."""
    sample_rate = CANONICAL_FORMAT.sample_rate
    num_samples = int(sample_rate * duration_ms / 1000)

    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        # Sine wave with some amplitude variation to simulate speech-like audio
        amplitude = 8000 * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * t))  # AM modulation
        sample = int(amplitude * math.sin(2 * math.pi * frequency * t))
        samples.append(max(-32768, min(32767, sample)))

    return struct.pack(f'<{num_samples}h', *samples)


async def demo_asr(verbose: bool = False):
    """Demonstrate ASR with faster-whisper."""
    print("\n" + "=" * 60)
    print("Demo 1: ASR (Automatic Speech Recognition)")
    print("=" * 60)

    try:
        # Initialize ASR
        logger.info("Loading faster-whisper model (base.en)...")
        asr = FasterWhisperASR(
            model_size="base.en",
            device="auto",  # Will use CUDA if available
        )
        await asr.initialize()

        # Health check
        health = await asr.health_check()
        logger.info(f"ASR health: {health}")

        # Generate test audio (silence with some noise)
        # In real use, this would be actual speech
        logger.info("Processing test audio (sine wave - won't transcribe to speech)...")
        test_audio = generate_test_audio(2000)

        # Process audio
        results = []
        async for result in asr.transcribe_stream(test_audio):
            results.append(result)
            if verbose:
                logger.info(f"  ASR result: {result}")

        logger.info(f"ASR processed {len(results)} segments")

        # Cleanup
        await asr.close()
        print("[OK] ASR demo completed successfully")
        return True

    except ImportError as e:
        logger.error(f"faster-whisper not installed: {e}")
        print("[SKIP] Install: pip install faster-whisper")
        return False
    except Exception as e:
        logger.error(f"ASR error: {e}")
        print(f"[FAIL] {e}")
        return False


async def demo_llm(model: str = "llama3.2", verbose: bool = False):
    """Demonstrate LLM with Ollama."""
    print("\n" + "=" * 60)
    print("Demo 2: LLM (Large Language Model)")
    print("=" * 60)

    try:
        # Initialize LLM
        logger.info(f"Connecting to Ollama with model: {model}...")
        llm = OllamaLLM(
            model=model,
            base_url="http://localhost:11434",
        )
        await llm.initialize()

        # Health check
        health = await llm.health_check()
        logger.info(f"LLM health: {health}")

        if not health.get("available"):
            logger.error("Ollama not available. Start with: ollama serve")
            print("[SKIP] Ollama server not running")
            return False

        # Generate response
        messages = [
            {"role": "system", "content": "You are a helpful voice assistant. Be very concise - respond in 1-2 sentences."},
            {"role": "user", "content": "What is the capital of France?"},
        ]

        logger.info("Generating response...")
        print("\n  Response: ", end="", flush=True)

        full_response = ""
        async for chunk in llm.generate_stream(messages):
            if chunk.text:
                print(chunk.text, end="", flush=True)
                full_response += chunk.text

        print("\n")
        logger.info(f"Generated {len(full_response)} characters")

        # Cleanup
        await llm.close()
        print("[OK] LLM demo completed successfully")
        return True

    except Exception as e:
        logger.error(f"LLM error: {e}")
        print(f"\n[FAIL] {e}")
        print("  Ensure Ollama is running: ollama serve && ollama pull llama3.2")
        return False


async def demo_tts(verbose: bool = False):
    """Demonstrate TTS with Piper."""
    print("\n" + "=" * 60)
    print("Demo 3: TTS (Text-to-Speech)")
    print("=" * 60)

    try:
        # Initialize TTS
        logger.info("Loading Piper TTS (en_US-lessac-medium)...")
        tts = PiperTTS(
            voice="en_US-lessac-medium",
        )
        await tts.initialize()

        # Health check
        health = await tts.health_check()
        logger.info(f"TTS health: {health}")

        # Synthesize speech
        text = "Hello! This is a test of the Piper text to speech system. It sounds quite natural."
        logger.info(f"Synthesizing: '{text}'")

        total_bytes = 0
        chunk_count = 0
        async for chunk in tts.synthesize_stream(text):
            total_bytes += len(chunk.audio_data)
            chunk_count += 1
            if verbose:
                logger.info(f"  TTS chunk {chunk_count}: {len(chunk.audio_data)} bytes")

        duration_ms = (total_bytes / 2) / CANONICAL_FORMAT.sample_rate * 1000
        logger.info(f"Generated {total_bytes} bytes ({chunk_count} chunks, ~{duration_ms:.0f}ms audio)")

        # Cleanup
        await tts.close()
        print("[OK] TTS demo completed successfully")
        return True

    except ImportError as e:
        logger.error(f"piper-tts not installed: {e}")
        print("[SKIP] Install: pip install piper-tts")
        return False
    except Exception as e:
        logger.error(f"TTS error: {e}")
        print(f"[FAIL] {e}")
        return False


async def demo_vad(verbose: bool = False):
    """Demonstrate VAD with Silero."""
    print("\n" + "=" * 60)
    print("Demo 4: VAD (Voice Activity Detection)")
    print("=" * 60)

    try:
        # Initialize VAD
        logger.info("Loading Silero VAD...")
        vad = SileroVAD()
        await vad.initialize()

        # Health check
        health = await vad.health_check()
        logger.info(f"VAD health: {health}")

        # Process test audio (simulated speech and silence)
        logger.info("Processing audio frames...")

        # Generate frames with varying amplitudes (simulating speech/silence)
        frame_size = CANONICAL_FORMAT.samples_per_frame * 2  # bytes

        speech_detected = 0
        silence_detected = 0

        for i in range(50):  # 50 frames = 1 second at 20ms frames
            # Alternate between "speech" (loud) and silence
            if i % 10 < 6:  # 60% speech-like
                audio = generate_test_audio(20, frequency=300 + i * 10)[:frame_size]
            else:  # 40% silence
                audio = b'\x00' * frame_size

            is_speech = await vad.process_frame(audio)
            if is_speech:
                speech_detected += 1
            else:
                silence_detected += 1

            if verbose and i % 10 == 0:
                logger.info(f"  Frame {i}: speech={is_speech}")

        logger.info(f"Detected: speech={speech_detected} frames, silence={silence_detected} frames")

        # Cleanup
        await vad.close()
        print("[OK] VAD demo completed successfully")
        return True

    except Exception as e:
        logger.error(f"VAD error: {e}")
        print(f"[FAIL] {e}")
        return False


async def demo_full_pipeline(model: str = "llama3.2", verbose: bool = False):
    """Demonstrate full pipeline: ASR -> LLM -> TTS."""
    print("\n" + "=" * 60)
    print("Demo 5: Full Pipeline (ASR -> LLM -> TTS)")
    print("=" * 60)

    results = {"asr": False, "llm": False, "tts": False}

    try:
        # Initialize all components
        logger.info("Initializing pipeline components...")

        # ASR
        try:
            asr = FasterWhisperASR(model_size="base.en", device="auto")
            await asr.initialize()
            results["asr"] = True
            logger.info("  ASR: ready")
        except Exception as e:
            logger.warning(f"  ASR: unavailable ({e})")
            asr = None

        # LLM
        try:
            llm = OllamaLLM(model=model, base_url="http://localhost:11434")
            await llm.initialize()
            health = await llm.health_check()
            if health.get("available"):
                results["llm"] = True
                logger.info("  LLM: ready")
            else:
                logger.warning("  LLM: Ollama not running")
                llm = None
        except Exception as e:
            logger.warning(f"  LLM: unavailable ({e})")
            llm = None

        # TTS
        try:
            tts = PiperTTS(voice="en_US-lessac-medium")
            await tts.initialize()
            results["tts"] = True
            logger.info("  TTS: ready")
        except Exception as e:
            logger.warning(f"  TTS: unavailable ({e})")
            tts = None

        # Run pipeline if LLM and TTS available (ASR optional for this demo)
        if llm and tts:
            # Simulated user input (in real scenario, comes from ASR)
            user_text = "Tell me a very short joke."
            logger.info(f"\nUser: {user_text}")

            # LLM generation
            messages = [
                {"role": "system", "content": "You are a funny voice assistant. Keep responses under 20 words."},
                {"role": "user", "content": user_text},
            ]

            print("\nAssistant: ", end="", flush=True)
            llm_response = ""
            async for chunk in llm.generate_stream(messages):
                if chunk.text:
                    print(chunk.text, end="", flush=True)
                    llm_response += chunk.text
            print()

            # TTS synthesis
            logger.info("\nSynthesizing speech...")
            total_audio_bytes = 0
            async for tts_chunk in tts.synthesize_stream(llm_response):
                total_audio_bytes += len(tts_chunk.audio_data)

            audio_duration_ms = (total_audio_bytes / 2) / CANONICAL_FORMAT.sample_rate * 1000
            logger.info(f"Generated {audio_duration_ms:.0f}ms of audio")

            print("\n[OK] Full pipeline demo completed successfully")
        else:
            missing = [k for k, v in results.items() if not v]
            print(f"\n[SKIP] Missing components: {', '.join(missing)}")

        # Cleanup
        if asr:
            await asr.close()
        if llm:
            await llm.close()
        if tts:
            await tts.close()

        return all(results.values())

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        print(f"[FAIL] {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Voice Agent Demo with Real Backends")
    parser.add_argument("--llm-model", default="llama3.2", help="Ollama model to use")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--demo", choices=["asr", "llm", "tts", "vad", "full", "all"],
                       default="all", help="Which demo to run")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print("\n" + "=" * 60)
    print("Voice Agent Demo - Real Local Backends")
    print("=" * 60)
    print(f"LLM Model: {args.llm_model}")
    print(f"Verbose: {args.verbose}")

    results = {}

    if args.demo in ("asr", "all"):
        results["ASR"] = await demo_asr(args.verbose)

    if args.demo in ("llm", "all"):
        results["LLM"] = await demo_llm(args.llm_model, args.verbose)

    if args.demo in ("tts", "all"):
        results["TTS"] = await demo_tts(args.verbose)

    if args.demo in ("vad", "all"):
        results["VAD"] = await demo_vad(args.verbose)

    if args.demo in ("full", "all"):
        results["Pipeline"] = await demo_full_pipeline(args.llm_model, args.verbose)

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for name, success in results.items():
        status = "[OK]" if success else "[FAIL/SKIP]"
        print(f"  {name}: {status}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nPassed: {passed}/{total}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
