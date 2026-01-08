#!/usr/bin/env python3
"""
End-to-end pipeline test for fake mode.

Tests each component in isolation, then integration:
1. Vosk STT - can it transcribe audio?
2. FakeLLM - can it generate responses?
3. Piper TTS - can it synthesize audio?
4. Integration - does the full pipeline work?
"""

import asyncio
import json
import logging
import sys
import wave
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def test_vosk_stt():
    """Test Vosk STT in isolation."""
    logger.info("=" * 80)
    logger.info("TEST 1: Vosk STT")
    logger.info("=" * 80)

    try:
        from vosk import Model, KaldiRecognizer

        # Create test audio with voice
        logger.info("Creating test audio file...")
        import subprocess
        subprocess.run([
            "say", "-o", "/tmp/test_voice.wav",
            "--data-format=LEI16@16000",
            "Hello world this is a test"
        ], check=True)

        # Test Vosk transcription
        model_path = Path.home() / ".cache/vosk/vosk-model-small-en-us-0.15"
        logger.info(f"Loading Vosk model from {model_path}")
        model = Model(str(model_path))
        recognizer = KaldiRecognizer(model, 16000)
        recognizer.SetWords(True)

        logger.info("Transcribing test audio...")
        with wave.open("/tmp/test_voice.wav", "rb") as w:
            while True:
                data = w.readframes(4000)
                if len(data) == 0:
                    break
                recognizer.AcceptWaveform(data)

        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "")

        logger.info(f"Transcription result: '{text}'")

        if "hello" in text.lower():
            logger.info("✅ Vosk STT: PASS")
            return True
        else:
            logger.error(f"❌ Vosk STT: FAIL - Expected 'hello' in transcript, got: '{text}'")
            return False

    except Exception as e:
        logger.error(f"❌ Vosk STT: FAIL - {e}", exc_info=True)
        return False


async def test_fake_llm():
    """Test FakeLLM in isolation."""
    logger.info("=" * 80)
    logger.info("TEST 2: FakeLLM")
    logger.info("=" * 80)

    try:
        from agent_playground.fake_llm import create_fake_llm
        from agent_playground.core.interfaces import ChatContext

        logger.info("Creating FakeLLM...")
        llm = create_fake_llm("helpful")

        logger.info("Creating chat context...")
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello, how are you?")

        logger.info("Generating response...")
        full_response = ""
        token_count = 0
        async for token in llm.chat(chat_ctx=chat_ctx):
            full_response = token.accumulated_text
            token_count += 1

        logger.info(f"Response: '{full_response}'")
        logger.info(f"Token count: {token_count}")

        if len(full_response) > 0 and token_count > 0:
            logger.info("✅ FakeLLM: PASS")
            return True
        else:
            logger.error("❌ FakeLLM: FAIL - No response generated")
            return False

    except Exception as e:
        logger.error(f"❌ FakeLLM: FAIL - {e}", exc_info=True)
        return False


async def test_piper_tts():
    """Test Piper TTS in isolation."""
    logger.info("=" * 80)
    logger.info("TEST 3: Piper TTS")
    logger.info("=" * 80)

    try:
        from agent_playground.local_agent import LocalPiperTTS

        logger.info("Creating LocalPiperTTS...")
        tts = LocalPiperTTS(
            voice="en_US-lessac-medium",
            output_sample_rate=24000,
        )

        logger.info("Synthesizing text...")

        async def text_gen():
            yield "Hello, this is a test."

        chunks = []
        async for chunk in tts.synthesize(text_gen()):
            chunks.append(chunk)
            logger.info(f"Got chunk: {len(chunk.audio_data)} bytes")

        total_bytes = sum(len(c.audio_data) for c in chunks)
        logger.info(f"Total audio: {total_bytes} bytes in {len(chunks)} chunks")

        if total_bytes > 0:
            logger.info("✅ Piper TTS: PASS")
            return True
        else:
            logger.error("❌ Piper TTS: FAIL - No audio generated")
            return False

    except Exception as e:
        logger.error(f"❌ Piper TTS: FAIL - {e}", exc_info=True)
        return False


async def test_session_creation():
    """Test AgentSession creation with fake mode."""
    logger.info("=" * 80)
    logger.info("TEST 4: AgentSession Creation")
    logger.info("=" * 80)

    try:
        from agent_playground.fake_llm import create_fake_llm
        from agent_playground.local_agent import create_selfhosted_session

        logger.info("Creating fake LLM...")
        fake_llm = create_fake_llm("helpful")

        logger.info("Creating selfhosted session with custom LLM...")
        session = create_selfhosted_session(
            vosk_model="vosk-model-small-en-us-0.15",
            custom_llm=fake_llm,
        )

        logger.info(f"Session created: {session}")
        logger.info(f"Session STT: {session.stt}")
        logger.info(f"Session LLM: {session.llm}")
        logger.info(f"Session TTS: {session.tts}")
        logger.info(f"Session VAD: {session.vad}")

        # Check that LLM is our FakeLLM
        if session.llm is fake_llm:
            logger.info("✅ Session has correct FakeLLM")
        else:
            logger.error(f"❌ Session LLM mismatch: expected {fake_llm}, got {session.llm}")
            return False

        # Check that STT exists
        if session.stt is not None:
            logger.info(f"✅ Session has STT: {type(session.stt).__name__}")
        else:
            logger.error("❌ Session missing STT")
            return False

        # TTS can be None (handled by LocalVoiceAssistant)
        logger.info(f"Session TTS: {session.tts}")

        logger.info("✅ AgentSession Creation: PASS")
        return True

    except Exception as e:
        logger.error(f"❌ AgentSession Creation: FAIL - {e}", exc_info=True)
        return False


async def test_llm_integration():
    """Test that the LLM can be called through the session."""
    logger.info("=" * 80)
    logger.info("TEST 5: LLM Integration")
    logger.info("=" * 80)

    try:
        from agent_playground.fake_llm import create_fake_llm
        from agent_playground.core.interfaces import ChatContext

        logger.info("Creating fake LLM...")
        fake_llm = create_fake_llm("helpful")

        # Simulate what AgentSession does
        logger.info("Testing LLM call...")
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello")

        response = ""
        token_count = 0
        async for token in fake_llm.chat(chat_ctx=chat_ctx):
            response = token.accumulated_text
            token_count += 1

        logger.info(f"LLM response: '{response}' ({token_count} tokens)")

        if len(response) > 0:
            logger.info("✅ LLM Integration: PASS")
            return True
        else:
            logger.error("❌ LLM Integration: FAIL - No response")
            return False

    except Exception as e:
        logger.error(f"❌ LLM Integration: FAIL - {e}", exc_info=True)
        return False


async def test_generate_reply():
    """Test session.generate_reply() - THIS IS THE CRITICAL TEST!"""
    logger.info("=" * 80)
    logger.info("TEST 6: session.generate_reply() [CRITICAL]")
    logger.info("=" * 80)

    try:
        from agent_playground.fake_llm import create_fake_llm
        from agent_playground.local_agent import create_selfhosted_session

        logger.info("Creating fake LLM...")
        fake_llm = create_fake_llm("helpful")

        logger.info("Creating selfhosted session with custom LLM...")
        session = create_selfhosted_session(
            vosk_model="vosk-model-small-en-us-0.15",
            custom_llm=fake_llm,
        )

        logger.info(f"Session LLM: {session.llm}")
        logger.info(f"Session LLM type: {type(session.llm).__name__}")

        # THIS IS WHAT LocalVoiceAssistant.on_enter() CALLS!
        logger.info("Calling session.generate_reply()...")
        logger.info("This should produce [FAKE_LLM] debug logs if it works!")

        # Try to call generate_reply like the agent does
        try:
            await session.generate_reply(
                instructions="Say hello to the user in a friendly way."
            )
            logger.info("generate_reply() completed without exception")

            # Check if we saw FAKE_LLM debug logs
            logger.info("✅ generate_reply() Test: PASS (check logs for [FAKE_LLM] activity)")
            return True

        except Exception as e:
            logger.error(f"generate_reply() raised exception: {e}", exc_info=True)
            logger.error("❌ generate_reply() Test: FAIL - Exception raised")
            return False

    except Exception as e:
        logger.error(f"❌ generate_reply() Test: FAIL - Setup failed: {e}", exc_info=True)
        return False


async def main():
    """Run all tests."""
    logger.info("\n" + "=" * 80)
    logger.info("FAKE MODE END-TO-END PIPELINE TEST")
    logger.info("=" * 80 + "\n")

    results = {}

    # Test 1: Vosk STT
    results["vosk_stt"] = test_vosk_stt()

    # Test 2: FakeLLM
    results["fake_llm"] = await test_fake_llm()

    # Test 3: Piper TTS
    results["piper_tts"] = await test_piper_tts()

    # Test 4: Session creation
    results["session_creation"] = await test_session_creation()

    # Test 5: LLM integration
    results["llm_integration"] = await test_llm_integration()

    # Test 6: CRITICAL - session.generate_reply()
    results["generate_reply"] = await test_generate_reply()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{test_name:30} {status}")

    all_passed = all(results.values())

    logger.info("=" * 80)
    if all_passed:
        logger.info("🎉 ALL TESTS PASSED")
        return 0
    else:
        logger.error("💥 SOME TESTS FAILED")
        failed = [name for name, passed in results.items() if not passed]
        logger.error(f"Failed tests: {', '.join(failed)}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
