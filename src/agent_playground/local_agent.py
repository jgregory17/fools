"""
Local Voice Assistant - Fully Self-Hosted Agent

A voice assistant that uses fully local inference:
- VAD: Silero VAD (local, downloads model on first run)
- STT: Faster-Whisper (local, wrapped for LiveKit streaming)
- LLM: Ollama (local)
- TTS: Piper (local)

No cloud APIs required. All processing happens on your hardware.

Usage:
    Set AGENT_MODE=selfhosted in environment, then run:
    python -m agent_playground.worker dev
"""

from __future__ import annotations

import asyncio
import logging
import struct
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterable, Optional

import numpy as np

from livekit import rtc
from livekit.agents import Agent, AgentSession, ModelSettings, stt, utils
from livekit.plugins import silero

from .modules.tts.piper_tts import PiperTTS

logger = logging.getLogger("local-agent")


# ─────────────────────────────────────────────────────────────────────────────
# Local Vosk STT - True Streaming Speech Recognition
# ─────────────────────────────────────────────────────────────────────────────

class VoskSpeechStream(stt.SpeechStream):
    """
    Vosk-based streaming speech recognition.

    Provides true real-time streaming with interim and final results.
    """

    def __init__(
        self,
        stt_instance,
        recognizer,
        language: str = "en",
        sample_rate: int = 16000,
        conn_options=None,
    ):
        super().__init__(
            stt=stt_instance,
            conn_options=conn_options,
            sample_rate=sample_rate,
        )
        self._recognizer = recognizer
        self._language = language
        self._speech_id_counter = 0
        self._speaking = False
        self._logger = logging.getLogger(__name__)
        self._logger.debug(f"[VOSK] VoskSpeechStream initialized: language={language}, sample_rate={sample_rate}")

    async def _run(self) -> None:
        """Main loop for streaming speech recognition."""
        import json

        self._logger.debug("[VOSK] _run() started, waiting for audio frames...")
        frame_count = 0

        try:
            # Process audio frames from the input channel
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    self._logger.debug(f"[VOSK] FlushSentinel received after {frame_count} frames")
                    # Get final result
                    result = json.loads(self._recognizer.FinalResult())
                    text = result.get("text", "").strip()
                    self._logger.debug(f"[VOSK] FlushSentinel final result: '{text}'")
                    if text:
                        self._logger.info(f"[VOSK] Sending FINAL_TRANSCRIPT: '{text}'")
                        self._event_ch.send_nowait(stt.SpeechEvent(
                            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[stt.SpeechData(
                                text=text,
                                language=self._language,
                                confidence=1.0,
                            )],
                            request_id=f"vosk_{self._speech_id_counter}",
                        ))
                        self._speech_id_counter += 1

                    if self._speaking:
                        self._speaking = False
                        self._logger.debug("[VOSK] Sending END_OF_SPEECH")
                        self._event_ch.send_nowait(
                            stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH)
                        )
                    frame_count = 0
                    continue

                # Process audio frame
                frame_count += 1
                audio_data = bytes(data.data)
                self._logger.debug(f"[VOSK] Frame {frame_count}: received {len(audio_data)} bytes")

                # Vosk accepts raw PCM16 audio
                accepted = self._recognizer.AcceptWaveform(audio_data)
                self._logger.debug(f"[VOSK] Frame {frame_count}: AcceptWaveform returned {accepted}")

                if accepted:
                    # Final result available
                    result = json.loads(self._recognizer.Result())
                    text = result.get("text", "").strip()
                    self._logger.debug(f"[VOSK] Frame {frame_count}: Final result: '{text}'")
                    if text:
                        if not self._speaking:
                            self._speaking = True
                            self._logger.debug("[VOSK] Sending START_OF_SPEECH")
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
                            )

                        self._logger.info(f"[VOSK] Sending FINAL_TRANSCRIPT: '{text}'")
                        self._event_ch.send_nowait(stt.SpeechEvent(
                            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[stt.SpeechData(
                                text=text,
                                language=self._language,
                                confidence=1.0,
                            )],
                            request_id=f"vosk_{self._speech_id_counter}",
                        ))
                        self._speech_id_counter += 1
                else:
                    # Partial result
                    partial = json.loads(self._recognizer.PartialResult())
                    text = partial.get("partial", "").strip()
                    self._logger.debug(f"[VOSK] Frame {frame_count}: Partial result: '{text}'")
                    if text:
                        if not self._speaking:
                            self._speaking = True
                            self._logger.debug("[VOSK] Sending START_OF_SPEECH")
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
                            )

                        self._logger.debug(f"[VOSK] Sending INTERIM_TRANSCRIPT: '{text}'")
                        self._event_ch.send_nowait(stt.SpeechEvent(
                            type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                            alternatives=[stt.SpeechData(
                                text=text,
                                language=self._language,
                                confidence=0.8,
                            )],
                            request_id=f"vosk_{self._speech_id_counter}",
                        ))
        except Exception as e:
            self._logger.error(f"[VOSK] Error in _run(): {e}", exc_info=True)
            raise
        finally:
            self._logger.debug(f"[VOSK] _run() ended after processing {frame_count} frames")


class LocalVoskSTT(stt.STT):
    """
    Local Vosk STT with true streaming support.

    Vosk provides real-time streaming speech recognition that runs entirely
    locally. Models are downloaded on first use.

    Installation:
        pip install vosk

    Model download (automatic or manual):
        Models are downloaded to ~/.cache/vosk/ on first use.
        Or download manually from https://alphacephei.com/vosk/models
    """

    def __init__(
        self,
        model_name: str = "vosk-model-small-en-us-0.15",
        model_path: Optional[str] = None,
        sample_rate: int = 16000,
        language: str = "en",
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True)
        )
        self._model_name = model_name
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._language = language
        self._model = None

    def _ensure_model(self):
        """Lazy-load the Vosk model."""
        if self._model is not None:
            return self._model

        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)  # Suppress Vosk logging
        except ImportError:
            raise ImportError(
                "vosk is required for LocalVoskSTT. "
                "Run: pip install vosk"
            )

        import os
        from pathlib import Path

        if self._model_path and os.path.exists(self._model_path):
            model_path = self._model_path
        else:
            # Try to find or download model
            cache_dir = Path.home() / ".cache" / "vosk"
            cache_dir.mkdir(parents=True, exist_ok=True)
            model_path = cache_dir / self._model_name

            if not model_path.exists():
                logger.info(f"Downloading Vosk model: {self._model_name}")
                # Download model
                import urllib.request
                import zipfile
                url = f"https://alphacephei.com/vosk/models/{self._model_name}.zip"
                zip_path = cache_dir / f"{self._model_name}.zip"

                try:
                    urllib.request.urlretrieve(url, zip_path)
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(cache_dir)
                    zip_path.unlink()
                    logger.info(f"Vosk model downloaded: {model_path}")
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to download Vosk model '{self._model_name}'. "
                        f"Download manually from https://alphacephei.com/vosk/models "
                        f"and set model_path. Error: {e}"
                    )

        logger.info(f"Loading Vosk model: {model_path}")
        self._model = Model(str(model_path))
        logger.info("Vosk model loaded")
        return self._model

    def stream(self, *, language: str | None = None, conn_options=None, **kwargs) -> VoskSpeechStream:
        """Create a streaming recognition session."""
        from vosk import KaldiRecognizer

        logger.debug(f"[VOSK] Creating streaming session: language={language or self._language}, sample_rate={self._sample_rate}")
        model = self._ensure_model()
        recognizer = KaldiRecognizer(model, self._sample_rate)
        recognizer.SetWords(True)
        logger.debug("[VOSK] KaldiRecognizer created successfully")

        stream = VoskSpeechStream(
            stt_instance=self,
            recognizer=recognizer,
            language=language or self._language,
            sample_rate=self._sample_rate,
            conn_options=conn_options,
        )
        logger.info(f"[VOSK] VoskSpeechStream created and ready")
        return stream

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: str | None = None,
    ) -> stt.SpeechEvent:
        """Recognize from a complete buffer (non-streaming fallback)."""
        from vosk import KaldiRecognizer
        import json

        model = self._ensure_model()
        recognizer = KaldiRecognizer(model, self._sample_rate)

        # Process all frames
        for frame in buffer:
            recognizer.AcceptWaveform(bytes(frame.data))

        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(
                text=text,
                language=language or self._language,
                confidence=1.0,
            )],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Local Piper TTS - LiveKit Plugin Compatible Wrapper
# ─────────────────────────────────────────────────────────────────────────────

class LocalPiperTTS:
    """
    Local Piper TTS wrapper for use with AgentSession.

    Piper is a fast, local neural TTS that runs entirely on CPU.
    Models are downloaded on first use.
    """

    def __init__(
        self,
        voice: str = "en_US-lessac-medium",
        model_path: Optional[str] = None,
        output_sample_rate: int = 24000,
    ):
        self._voice = voice
        self._model_path = model_path
        self._output_sample_rate = output_sample_rate
        self._piper: Optional[PiperTTS] = None

    def _ensure_piper(self) -> PiperTTS:
        """Lazy-load the Piper TTS backend."""
        if self._piper is None:
            logger.info(f"Loading Piper TTS: voice={self._voice}")
            self._piper = PiperTTS(
                voice=self._voice,
                model_path=self._model_path,
            )
        return self._piper

    async def synthesize(self, text: str) -> AsyncIterable[rtc.AudioFrame]:
        """Synthesize text to audio frames."""
        piper = self._ensure_piper()

        async def text_gen():
            yield text

        async for chunk in piper.synthesize(text_gen()):
            audio_data = chunk.audio_data
            source_rate = chunk.sample_rate

            if source_rate != self._output_sample_rate:
                audio_data = self._resample_audio(audio_data, source_rate, self._output_sample_rate)

            num_samples = len(audio_data) // 2

            yield rtc.AudioFrame(
                data=audio_data,
                sample_rate=self._output_sample_rate,
                num_channels=1,
                samples_per_channel=num_samples,
            )

    def _resample_audio(self, audio_data: bytes, from_rate: int, to_rate: int) -> bytes:
        """Resample PCM16 audio."""
        if from_rate == to_rate:
            return audio_data

        num_samples = len(audio_data) // 2
        samples = np.array(struct.unpack(f'<{num_samples}h', audio_data), dtype=np.float32)

        ratio = to_rate / from_rate
        new_length = int(len(samples) * ratio)

        old_indices = np.arange(len(samples))
        new_indices = np.linspace(0, len(samples) - 1, new_length)
        resampled = np.interp(new_indices, old_indices, samples)

        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()

    async def close(self):
        if self._piper:
            await self._piper.close()
            self._piper = None


# ─────────────────────────────────────────────────────────────────────────────
# Local Voice Assistant
# ─────────────────────────────────────────────────────────────────────────────


class LocalVoiceAssistant(Agent):
    """
    Voice assistant using fully local inference - no cloud APIs required.

    Uses:
    - Silero VAD for voice activity detection (local, ~2MB model)
    - Vosk for streaming STT (local, true real-time streaming)
    - Piper for TTS (local, fast neural synthesis)
    - Ollama for LLM (local, passed via AgentSession)
    """

    def __init__(
        self,
        instructions: str = "You are a helpful voice AI assistant.",
        # TTS settings
        piper_voice: str = "en_US-lessac-medium",
        piper_model_path: Optional[str] = None,
        # Audio settings
        output_sample_rate: int = 24000,
    ) -> None:
        super().__init__(instructions=instructions)
        self._piper_voice = piper_voice
        self._piper_model_path = piper_model_path
        self._output_sample_rate = output_sample_rate
        self._tts: Optional[LocalPiperTTS] = None

    def _ensure_tts(self) -> LocalPiperTTS:
        """Lazy-load the Piper TTS backend."""
        if self._tts is None:
            self._tts = LocalPiperTTS(
                voice=self._piper_voice,
                model_path=self._piper_model_path,
                output_sample_rate=self._output_sample_rate,
            )
        return self._tts

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncIterable[rtc.AudioFrame]:
        """
        Override TTS node to use local Piper.

        Receives streaming text from LLM, synthesizes to audio using Piper,
        and yields LiveKit AudioFrames.
        """
        logger.debug("LocalVoiceAssistant: Starting local TTS node")
        piper = self._ensure_tts()._ensure_piper()

        # Run Piper TTS on the text stream
        async for chunk in piper.synthesize(text):
            audio_data = chunk.audio_data
            source_rate = chunk.sample_rate

            if source_rate != self._output_sample_rate:
                audio_data = self._ensure_tts()._resample_audio(
                    audio_data, source_rate, self._output_sample_rate
                )

            num_samples = len(audio_data) // 2

            yield rtc.AudioFrame(
                data=audio_data,
                sample_rate=self._output_sample_rate,
                num_channels=1,
                samples_per_channel=num_samples,
            )

    async def on_enter(self) -> None:
        """Called when agent becomes active - generate greeting."""
        logger.info("LocalVoiceAssistant: Agent entered session")
        logger.debug(f"LocalVoiceAssistant: Session LLM type: {type(self.session.llm).__name__}")
        logger.debug(f"LocalVoiceAssistant: Session LLM object: {self.session.llm}")

        try:
            logger.info("LocalVoiceAssistant: Calling session.generate_reply() for greeting")
            await self.session.generate_reply(
                instructions="Greet the user warmly and let them know you're ready to help.",
            )
            logger.info("LocalVoiceAssistant: session.generate_reply() completed successfully")
        except Exception as e:
            logger.error(f"LocalVoiceAssistant: session.generate_reply() failed: {e}", exc_info=True)

    async def close(self) -> None:
        """Release local model resources."""
        logger.info("LocalVoiceAssistant: Closing local backends")
        if self._tts is not None:
            await self._tts.close()
            self._tts = None


# ─────────────────────────────────────────────────────────────────────────────
# Factory function for creating self-hosted AgentSession
# ─────────────────────────────────────────────────────────────────────────────

def create_selfhosted_session(
    # STT settings
    vosk_model: str = "vosk-model-small-en-us-0.15",
    vosk_model_path: Optional[str] = None,
    # LLM settings (Ollama)
    ollama_model: str = "llama3.2",
    ollama_base_url: str = "http://localhost:11434/v1",
    # Optional: pass a custom LLM instance (for fake mode)
    custom_llm: Optional[Any] = None,
    # VAD settings
    vad_min_speech_duration: float = 0.1,
    vad_min_silence_duration: float = 0.5,
    # Turn detection settings
    use_turn_detector: bool = True,
    min_endpointing_delay: float = 0.5,
    max_endpointing_delay: float = 6.0,
) -> "AgentSession":
    """
    Create a fully self-hosted AgentSession with local STT/LLM/TTS.

    All inference runs locally - no cloud APIs required.

    Components:
    - VAD: Silero VAD (~2MB, local)
    - Turn Detection: LiveKit turn detector (~400MB, local, open-weights)
    - STT: Vosk streaming (~50MB, local)
    - LLM: Ollama (local)
    - TTS: Piper (handled by LocalVoiceAssistant)

    Usage:
        session = create_selfhosted_session(
            vosk_model="vosk-model-small-en-us-0.15",
            ollama_model="llama3.2",
        )
        await session.start(room=ctx.room, agent=LocalVoiceAssistant())

    Note: Run `python -m agent_playground.worker download-files` first to
    download the turn detector model weights.
    """
    from livekit.agents import AgentSession
    from livekit.plugins import openai as openai_plugin

    # Local VAD (Silero - downloads ~2MB model on first use)
    vad = silero.VAD.load(
        min_speech_duration=vad_min_speech_duration,
        min_silence_duration=vad_min_silence_duration,
    )

    # Local streaming STT (Vosk)
    local_stt = LocalVoskSTT(
        model_name=vosk_model,
        model_path=vosk_model_path,
        sample_rate=16000,
        language="en",
    )

    # Local LLM (Ollama via OpenAI-compatible API)
    # Or use custom_llm if provided (for fake mode)
    if custom_llm is not None:
        local_llm = custom_llm
        logger.info(f"Using custom LLM: {type(custom_llm).__name__}")
    else:
        local_llm = openai_plugin.LLM.with_ollama(
            model=ollama_model,
            base_url=ollama_base_url,
            temperature=0.7,
        )

    # Turn detection - open-weights model that runs locally on CPU (<500MB RAM)
    # Improves conversation flow by predicting when user has finished speaking
    turn_detection = None
    if use_turn_detector:
        try:
            from livekit.plugins.turn_detector.multilingual import MultilingualModel
            turn_detection = MultilingualModel()
            logger.info("Turn detector model enabled (local, ~400MB)")
        except (ImportError, RuntimeError) as e:
            logger.warning(
                f"Turn detector not available ({e.__class__.__name__}). "
                "Falling back to VAD-only mode. "
                "To enable turn detection, run: "
                "pip install 'livekit-agents[turn-detector]' && "
                "python -m agent_playground.worker download-files"
            )
            turn_detection = "vad"  # Fall back to VAD-only
    else:
        turn_detection = "vad"

    logger.info(f"Creating self-hosted session: STT={vosk_model}, LLM={ollama_model}")

    # Note: TTS is handled by LocalVoiceAssistant's tts_node override
    return AgentSession(
        stt=local_stt,
        llm=local_llm,
        tts=None,  # Will be handled by LocalVoiceAssistant.tts_node()
        vad=vad,
        turn_detection=turn_detection,
        min_endpointing_delay=min_endpointing_delay,
        max_endpointing_delay=max_endpointing_delay,
        # Interruption handling (all local, no cloud needed)
        allow_interruptions=True,
        min_interruption_duration=0.5,
        resume_false_interruption=True,
        false_interruption_timeout=2.0,
    )


def get_local_instructions(config_name: str = "default") -> str:
    """Get instructions for local voice assistant."""
    instructions_map = {
        "default": """You are a helpful voice AI assistant running entirely on local hardware.

You should:
- Be concise and conversational (this is a voice interface)
- Respond in 1-3 sentences unless more detail is requested
- Be friendly and professional
- Let users know if they ask that you run without any cloud dependencies

Remember: Users are speaking to you, not typing. Keep responses natural and brief.""",

        "creative_writer": """You are a creative writing assistant running on local hardware.

You help users with:
- Creative writing and brainstorming
- Story development and plot ideas
- Character creation
- Poetry and prose

Be imaginative but keep voice responses concise. Elaborate only when asked.""",

        "code_assistant": """You are a programming assistant running on local hardware.

You help users with:
- Explaining code concepts
- Debugging strategies
- Best practices
- Code architecture

Keep explanations clear and concise for voice. Avoid reading long code blocks aloud.""",
    }
    return instructions_map.get(config_name, instructions_map["default"])
