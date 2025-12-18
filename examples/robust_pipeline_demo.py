#!/usr/bin/env python3
"""
Robust Pipeline Demo.

Demonstrates all architectural components that prevent common voice agent pitfalls:

1. AudioNormalizer - Canonical format, resampling at boundaries
2. BackpressurePolicy - Bounded queues with drop policies
3. BehaviorPolicy - Echo suppression during speech
4. InterruptController - VAD-based barge-in handling
5. ConversationMemory - Context window management
6. ResourceManager - Device coordination

Usage:
    python examples/robust_pipeline_demo.py
"""

import asyncio
import logging
import struct
import sys
import time
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_playground.core.audio import (
    AudioNormalizer,
    AudioNormalizerConfig,
    CANONICAL_FORMAT,
    create_silence,
    compute_audio_energy,
)
from agent_playground.core.scheduler import (
    PipelineScheduler,
    PipelineSchedulerConfig,
    BackpressureMode,
    BoundedQueue,
    BackpressurePolicy,
    LatencyTracker,
)
from agent_playground.core.policies import (
    BehaviorPolicy,
    EchoSuppressionConfig,
    InterruptionConfig,
    TurnTakingConfig,
    SpeakingGate,
)
from agent_playground.core.interrupts import (
    InterruptController,
    InterruptControllerConfig,
    InterruptCoordinator,
)
from agent_playground.core.resources import (
    ResourceManager,
    ResourceManagerConfig,
)
from agent_playground.modules.vad import EnergyVAD, VADResult
from agent_playground.modules.memory import (
    ConversationMemory,
    ConversationMemoryConfig,
    SimpleSummarizer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
)
logger = logging.getLogger(__name__)


def generate_tone(frequency_hz: float, duration_ms: int, sample_rate: int = 24000) -> bytes:
    """Generate a sine wave tone as PCM16 audio."""
    import math

    samples = int(sample_rate * duration_ms / 1000)
    audio = []

    for i in range(samples):
        t = i / sample_rate
        sample = int(16000 * math.sin(2 * math.pi * frequency_hz * t))
        audio.append(max(-32768, min(32767, sample)))

    return struct.pack(f'<{len(audio)}h', *audio)


def generate_speech_simulation(duration_ms: int, sample_rate: int = 24000) -> bytes:
    """Generate audio that simulates speech (varying amplitude)."""
    import math
    import random

    samples = int(sample_rate * duration_ms / 1000)
    audio = []

    # Simulate speech with varying frequencies and amplitudes
    for i in range(samples):
        t = i / sample_rate

        # Mix of frequencies to simulate speech
        base_freq = 150 + 50 * math.sin(2 * math.pi * 3 * t)  # Varying pitch
        amplitude = 8000 * (0.5 + 0.5 * math.sin(2 * math.pi * 5 * t))  # Varying loudness

        sample = int(amplitude * math.sin(2 * math.pi * base_freq * t))
        sample += random.randint(-500, 500)  # Add some noise

        audio.append(max(-32768, min(32767, sample)))

    return struct.pack(f'<{len(audio)}h', *audio)


async def demo_audio_normalizer():
    """Demonstrate audio format normalization."""
    logger.info("=" * 60)
    logger.info("DEMO 1: Audio Normalizer")
    logger.info("=" * 60)

    normalizer = AudioNormalizer(AudioNormalizerConfig(
        target_sample_rate=24000,
        target_channels=1,
        target_frame_duration_ms=20,
    ))

    # Simulate audio coming in at different rates
    test_cases = [
        ("48kHz stereo", 48000, 2),
        ("16kHz mono", 16000, 1),
        ("44.1kHz mono", 44100, 1),
    ]

    for name, input_rate, channels in test_cases:
        # Generate 100ms of test audio
        samples = int(input_rate * 0.1)
        audio_data = generate_tone(440, 100, input_rate)

        # If stereo, duplicate samples
        if channels == 2:
            mono_samples = struct.unpack(f'<{len(audio_data)//2}h', audio_data)
            stereo_samples = []
            for s in mono_samples:
                stereo_samples.extend([s, s])  # L, R
            audio_data = struct.pack(f'<{len(stereo_samples)}h', *stereo_samples)

        # Normalize
        normalized_frames = normalizer.normalize(
            audio_data,
            input_sample_rate=input_rate,
            input_channels=channels,
        )

        logger.info(
            f"  {name}: {len(audio_data)} bytes -> "
            f"{len(normalized_frames)} frames @ 24kHz mono"
        )

    # Flush remaining
    final_frames = normalizer.flush()
    logger.info(f"  Flushed: {len(final_frames)} remaining frames")
    logger.info(f"  Metrics: {normalizer.metrics}")
    logger.info("")


async def demo_backpressure():
    """Demonstrate backpressure handling."""
    logger.info("=" * 60)
    logger.info("DEMO 2: Backpressure Policy")
    logger.info("=" * 60)

    # Create bounded queue with small size to trigger backpressure
    queue: BoundedQueue[int] = BoundedQueue(
        "demo_queue",
        BackpressurePolicy(
            mode=BackpressureMode.DROP_OLDEST,
            max_queue_size=5,
            log_drops=True,
            log_every_n_drops=1,
        )
    )

    # Add items rapidly to trigger backpressure
    logger.info("  Adding 20 items to queue with max size 5...")

    for i in range(20):
        added = queue.put_nowait(i)
        if not added:
            logger.info(f"    Item {i} dropped (queue full)")

    # Read remaining items
    items = []
    while True:
        item = queue.get_nowait()
        if item is None:
            break
        items.append(item)

    logger.info(f"  Remaining items in queue: {items}")
    logger.info(f"  Metrics: {queue.metrics.to_dict()}")
    logger.info("")


async def demo_echo_suppression():
    """Demonstrate echo suppression during speech."""
    logger.info("=" * 60)
    logger.info("DEMO 3: Echo Suppression (BehaviorPolicy)")
    logger.info("=" * 60)

    policy = BehaviorPolicy(
        echo_config=EchoSuppressionConfig(
            enabled=True,
            suppress_asr_during_speech=True,
            post_speech_suppression_ms=100,
        ),
    )

    # Simulate ASR processing during different states
    test_audio_energy = 0.5  # Normal speech energy

    # Before speaking
    should_process = policy.should_process_audio(test_audio_energy)
    logger.info(f"  Before speech: should_process_audio = {should_process}")

    # Start speaking
    policy.on_speech_start()
    should_process = policy.should_process_audio(test_audio_energy)
    logger.info(f"  During speech: should_process_audio = {should_process} (suppressed!)")

    # Stop speaking
    policy.on_speech_end()

    # Immediately after
    should_process = policy.should_process_audio(test_audio_energy)
    logger.info(f"  Just after speech: should_process_audio = {should_process} (post-suppression)")

    # Wait for suppression period
    await asyncio.sleep(0.15)
    should_process = policy.should_process_audio(test_audio_energy)
    logger.info(f"  After suppression period: should_process_audio = {should_process}")

    logger.info(f"  Metrics: {policy.metrics}")
    logger.info("")


async def demo_interrupt_controller():
    """Demonstrate VAD-based interruption handling."""
    logger.info("=" * 60)
    logger.info("DEMO 4: Interrupt Controller (Barge-in)")
    logger.info("=" * 60)

    # Create VAD and interrupt controller
    vad = EnergyVAD()

    interrupted_events = []

    async def on_interrupt(event):
        interrupted_events.append(event)
        logger.info(f"  >>> INTERRUPT DETECTED! Played {event.played_duration_ms:.0f}ms before interrupt")

    controller = InterruptController(
        config=InterruptControllerConfig(
            enabled=True,
            vad_threshold=0.3,
            min_speech_duration_ms=50,
            min_consecutive_frames=2,
            grace_period_ms=100,
        ),
        on_interrupt=on_interrupt,
    )

    # Simulate agent speaking
    logger.info("  Agent starts speaking...")
    controller.on_speech_start()

    # Wait past grace period
    await asyncio.sleep(0.15)

    # Simulate silence (no interrupt)
    silence = create_silence(20)
    vad_result = vad.process(silence)
    triggered = controller.process_vad(vad_result.probability)
    logger.info(f"  Silence: VAD={vad_result.probability:.2f}, triggered={triggered}")

    # Track some played audio
    controller.on_audio_played(500)

    # Simulate user speech (should trigger interrupt)
    speech = generate_speech_simulation(100)
    for _ in range(5):  # Multiple frames to meet threshold
        vad_result = vad.process(speech[:960])  # 20ms at 24kHz
        triggered = controller.process_vad(vad_result.probability)
        if triggered:
            break

    logger.info(f"  Speech detected: VAD={vad_result.probability:.2f}, triggered={triggered}")

    # Wait for async callback
    await asyncio.sleep(0.1)

    controller.on_speech_end()
    logger.info(f"  Total interrupts: {len(interrupted_events)}")
    logger.info(f"  Metrics: {controller.metrics}")
    logger.info("")


async def demo_conversation_memory():
    """Demonstrate context window management."""
    logger.info("=" * 60)
    logger.info("DEMO 5: Conversation Memory")
    logger.info("=" * 60)

    memory = ConversationMemory(
        config=ConversationMemoryConfig(
            max_turns=10,
            max_context_tokens=500,  # Small for demo
            summarize_threshold=0.6,
            auto_summarize=False,  # Manual for demo
        ),
        summarizer=SimpleSummarizer(),
    )

    # Add system message
    memory.add_system_message("You are a helpful assistant.")

    # Simulate a conversation
    conversation = [
        ("user", "Hello, I need help with my order."),
        ("assistant", "Of course! I'd be happy to help. What's your order number?"),
        ("user", "It's order #12345."),
        ("assistant", "I found order #12345. It shows as shipped yesterday."),
        ("user", "Great! When will it arrive?"),
        ("assistant", "Based on the tracking, it should arrive by Friday."),
        ("user", "Perfect, thank you!"),
        ("assistant", "You're welcome! Is there anything else I can help with?"),
    ]

    for role, content in conversation:
        if role == "user":
            memory.add_user_turn(content)
        else:
            memory.add_assistant_turn(content)

    logger.info(f"  Added {len(conversation)} turns")
    logger.info(f"  Estimated tokens: {memory.estimated_tokens}")
    logger.info(f"  Should summarize: {memory.should_summarize()}")

    # Get messages
    messages = memory.get_messages()
    logger.info(f"  Message count for LLM: {len(messages)}")

    # Force summarization
    if memory.should_summarize():
        summary = await memory.summarize_old_turns()
        logger.info(f"  Summarized! New token count: {memory.estimated_tokens}")
        logger.info(f"  Summary preview: {summary[:100] if summary else 'None'}...")

    logger.info(f"  Metrics: {memory.metrics}")
    logger.info("")


async def demo_resource_manager():
    """Demonstrate GPU/device coordination."""
    logger.info("=" * 60)
    logger.info("DEMO 6: Resource Manager")
    logger.info("=" * 60)

    manager = ResourceManager(ResourceManagerConfig(
        device_assignments={
            "asr": "auto",
            "llm": "auto",
            "tts": "cpu",
            "vad": "cpu",
        },
        verbose_logging=True,
    ))

    logger.info(f"  Available devices: {manager.available_devices}")

    # Get device assignments
    for module in ["asr", "llm", "tts", "vad"]:
        device = manager.get_device(module)
        logger.info(f"  {module} -> {device.device_string}")
        manager.register_allocation(module, device, estimated_mb=100 if module != "vad" else 10)

    logger.info(f"  Allocations: {manager.allocations}")
    logger.info(f"  Metrics: {manager.metrics}")
    logger.info("")


async def demo_full_pipeline():
    """Demonstrate all components working together."""
    logger.info("=" * 60)
    logger.info("DEMO 7: Full Pipeline Integration")
    logger.info("=" * 60)

    # Initialize all components
    scheduler = PipelineScheduler(PipelineSchedulerConfig(
        asr_queue_size=50,
        backpressure_mode=BackpressureMode.DROP_OLDEST,
    ))

    normalizer = AudioNormalizer()
    vad = EnergyVAD()
    policy = BehaviorPolicy()
    memory = ConversationMemory(summarizer=SimpleSummarizer())
    resources = ResourceManager()
    latency_tracker = LatencyTracker("demo")

    await scheduler.start()

    # Simulate a complete turn
    logger.info("  Simulating a complete voice turn...")

    # 1. Audio comes in
    span_id = "turn_001"
    latency_tracker.start_span(span_id, "total")
    latency_tracker.start_span(span_id, "asr")

    raw_audio = generate_speech_simulation(500)
    normalized = normalizer.normalize(raw_audio, input_sample_rate=24000)
    logger.info(f"  1. Received and normalized {len(normalized)} audio frames")

    # 2. VAD detects speech
    speech_detected = False
    for frame in normalized:
        vad_result = vad.process(frame)
        if vad_result.is_speech:
            speech_detected = True
            break

    logger.info(f"  2. VAD detected speech: {speech_detected}")

    # 3. Policy check (echo suppression)
    can_process = policy.should_process_audio(0.5)
    logger.info(f"  3. Policy allows processing: {can_process}")

    # 4. Queue audio for ASR
    for frame in normalized:
        await scheduler.audio_in.put(frame)
    logger.info(f"  4. Queued {len(normalized)} frames for ASR")

    latency_tracker.end_span(span_id, "asr")
    latency_tracker.start_span(span_id, "llm")

    # 5. Simulate transcript -> LLM
    transcript = "What's the weather like today?"
    memory.add_user_turn(transcript)
    await scheduler.transcripts.put(transcript)
    logger.info(f"  5. Transcript: '{transcript}'")

    latency_tracker.end_span(span_id, "llm")
    latency_tracker.start_span(span_id, "tts")

    # 6. Simulate LLM response -> TTS
    response = "It's sunny and 72 degrees."
    memory.add_assistant_turn(response)
    await scheduler.tts_input.put(response)
    logger.info(f"  6. Response: '{response}'")

    # 7. Agent speaks (with echo suppression)
    policy.on_speech_start()
    logger.info("  7. Agent speaking (ASR suppressed)")

    # Simulate TTS output
    tts_audio = generate_tone(200, 100)
    await scheduler.audio_out.put(tts_audio)

    policy.on_speech_end()
    logger.info("  8. Agent finished speaking")

    latency_tracker.end_span(span_id, "tts")
    timing = latency_tracker.complete_span(span_id)

    # Report metrics
    logger.info("")
    logger.info("  --- Metrics Summary ---")
    logger.info(f"  Scheduler: {scheduler.get_metrics()}")
    logger.info(f"  Memory: {memory.metrics}")
    logger.info(f"  Latency: {timing}")

    await scheduler.stop()
    logger.info("")


async def main():
    """Run all demos."""
    logger.info("")
    logger.info("ROBUST PIPELINE DEMO")
    logger.info("Demonstrating architectural solutions to common voice agent pitfalls")
    logger.info("")

    await demo_audio_normalizer()
    await demo_backpressure()
    await demo_echo_suppression()
    await demo_interrupt_controller()
    await demo_conversation_memory()
    await demo_resource_manager()
    await demo_full_pipeline()

    logger.info("=" * 60)
    logger.info("All demos complete!")
    logger.info("")
    logger.info("Key takeaways:")
    logger.info("  - AudioNormalizer handles sample rate mismatches automatically")
    logger.info("  - BoundedQueue with BackpressurePolicy prevents memory growth")
    logger.info("  - BehaviorPolicy suppresses ASR during speech to prevent echo")
    logger.info("  - InterruptController enables responsive barge-in handling")
    logger.info("  - ConversationMemory prevents context window overflow")
    logger.info("  - ResourceManager coordinates GPU usage across modules")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
