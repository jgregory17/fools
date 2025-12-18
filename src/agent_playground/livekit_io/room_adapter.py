"""
LiveKit Room Adapter.

Bridges LiveKit room events to the agent pipeline with robust lifecycle management.

Key Features:
- Proper track subscribe/unsubscribe handling
- Audio normalization to canonical format
- Backpressure-aware queuing
- Clean resource cleanup on disconnect/reconnect

Based on LiveKit Agents SDK patterns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Dict, Set
from enum import Enum, auto

from ..core.event_bus import EventBus
from ..core.events import (
    AgentState,
    AgentStateChange,
    AudioFrameIn,
    AudioFrameOut,
    InterruptionDetected,
    VADEvent,
)
from ..core.audio import (
    AudioNormalizer,
    AudioNormalizerConfig,
    CANONICAL_FORMAT,
    AudioFormat,
)
from ..core.scheduler import BoundedQueue, BackpressurePolicy, BackpressureMode

logger = logging.getLogger(__name__)

# Import LiveKit RTC - required for real LiveKit integration
try:
    from livekit import rtc
    LIVEKIT_AVAILABLE = True
except ImportError:
    rtc = None
    LIVEKIT_AVAILABLE = False
    logger.warning("livekit not installed. Run: pip install livekit")

# Type aliases for LiveKit types (fallback for type hints when livekit not installed)
Room = Any
RemoteParticipant = Any
LocalParticipant = Any
RemoteTrack = Any
LocalAudioTrack = Any
AudioFrame = Any


class TrackState(Enum):
    """State of a tracked audio source."""
    PENDING = auto()
    SUBSCRIBED = auto()
    UNSUBSCRIBED = auto()
    ERROR = auto()


@dataclass
class TrackedParticipant:
    """Tracks state for a participant's audio."""
    identity: str
    participant: Optional[RemoteParticipant] = None
    audio_track: Optional[RemoteTrack] = None
    track_state: TrackState = TrackState.PENDING
    read_task: Optional[asyncio.Task] = None
    subscribed_at: Optional[float] = None
    last_audio_at: Optional[float] = None
    frames_received: int = 0


@dataclass
class AudioInputOptions:
    """Configuration for audio input from room."""

    # Target format (will normalize to this)
    sample_rate: int = CANONICAL_FORMAT.sample_rate
    num_channels: int = CANONICAL_FORMAT.num_channels
    frame_duration_ms: int = CANONICAL_FORMAT.frame_duration_ms

    # Backpressure settings
    max_queue_size: int = 100  # ~2 seconds at 20ms frames
    backpressure_mode: BackpressureMode = BackpressureMode.DROP_OLDEST

    # Pre-buffer settings
    pre_connect_audio: bool = True
    pre_connect_audio_timeout: float = 3.0

    # Audio processing
    auto_resample: bool = True
    log_audio_stats: bool = False
    stats_interval_seconds: float = 30.0


@dataclass
class AudioOutputOptions:
    """Configuration for audio output to room."""

    sample_rate: int = CANONICAL_FORMAT.sample_rate
    num_channels: int = CANONICAL_FORMAT.num_channels
    track_name: str = "agent_audio"

    # Backpressure
    max_queue_size: int = 100
    backpressure_mode: BackpressureMode = BackpressureMode.DROP_OLDEST


@dataclass
class RoomOptions:
    """Configuration for room interaction."""

    audio_input: AudioInputOptions = field(default_factory=AudioInputOptions)
    audio_output: AudioOutputOptions = field(default_factory=AudioOutputOptions)

    # Participant linking
    participant_identity: Optional[str] = None
    link_timeout: float = 30.0

    # Lifecycle
    close_on_participant_leave: bool = True
    reconnect_on_disconnect: bool = True
    max_reconnect_attempts: int = 3

    # Room management
    delete_room_on_close: bool = False


class RoomAdapter:
    """
    Adapter between LiveKit room and agent pipeline with robust lifecycle.

    Handles:
    - Audio track subscription with proper state management
    - Audio normalization to canonical format
    - Backpressure-aware queuing
    - Clean reconnect and cleanup on participant leave

    Usage:
        adapter = RoomAdapter(room, event_bus, options)
        await adapter.start()

        # Audio flows through event_bus as AudioFrameIn/AudioFrameOut
        # Or use bounded queues directly:
        frame = await adapter.audio_in_queue.get()

        await adapter.stop()
    """

    def __init__(
        self,
        room: Room,
        event_bus: EventBus,
        options: Optional[RoomOptions] = None,
        agent_id: Optional[str] = None,
    ):
        self._room = room
        self._event_bus = event_bus
        self._options = options or RoomOptions()
        self._agent_id = agent_id

        # State
        self._running = False
        self._closing = False

        # Participant tracking
        self._tracked_participants: Dict[str, TrackedParticipant] = {}
        self._primary_participant: Optional[str] = None

        # Audio normalization
        self._input_normalizer = AudioNormalizer(AudioNormalizerConfig(
            target_sample_rate=self._options.audio_input.sample_rate,
            target_channels=self._options.audio_input.num_channels,
            target_frame_duration_ms=self._options.audio_input.frame_duration_ms,
        ))

        # Bounded queues for audio
        self.audio_in_queue: BoundedQueue[AudioFrameIn] = BoundedQueue(
            "room_audio_in",
            BackpressurePolicy(
                mode=self._options.audio_input.backpressure_mode,
                max_queue_size=self._options.audio_input.max_queue_size,
            )
        )

        self.audio_out_queue: BoundedQueue[AudioFrameOut] = BoundedQueue(
            "room_audio_out",
            BackpressurePolicy(
                mode=self._options.audio_output.backpressure_mode,
                max_queue_size=self._options.audio_output.max_queue_size,
            )
        )

        # Published track
        self._audio_source: Any = None
        self._published_track: Optional[LocalAudioTrack] = None

        # Tasks
        self._tasks: Set[asyncio.Task] = set()
        self._stats_task: Optional[asyncio.Task] = None

        # Metrics
        self._metrics = {
            "frames_received": 0,
            "frames_published": 0,
            "reconnects": 0,
            "track_errors": 0,
        }

    async def start(self) -> None:
        """Start the room adapter with full lifecycle setup."""
        if self._running:
            return

        logger.info(f"RoomAdapter starting for agent {self._agent_id}")
        self._running = True
        self._closing = False

        # Set up room event handlers
        self._setup_room_handlers()

        # Link to participant(s)
        await self._link_participants()

        # Start audio output publisher
        publish_task = asyncio.create_task(self._publish_audio_loop())
        self._tasks.add(publish_task)
        publish_task.add_done_callback(self._tasks.discard)

        # Start stats logging if enabled
        if self._options.audio_input.log_audio_stats:
            self._stats_task = asyncio.create_task(self._log_stats_loop())

        logger.info(f"RoomAdapter started for agent {self._agent_id}")

    async def stop(self) -> None:
        """Stop the room adapter with clean resource release."""
        if not self._running:
            return

        logger.info(f"RoomAdapter stopping for agent {self._agent_id}")
        self._closing = True
        self._running = False

        # Stop stats task
        if self._stats_task:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass

        # Stop all participant read tasks
        for tracked in self._tracked_participants.values():
            await self._cleanup_participant(tracked)

        # Cancel remaining tasks
        for task in list(self._tasks):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()

        # Unpublish track
        await self._unpublish_track()

        # Close queues
        self.audio_in_queue.close()
        self.audio_out_queue.close()

        # Clear state
        self._tracked_participants.clear()
        self._primary_participant = None

        logger.info(f"RoomAdapter stopped for agent {self._agent_id}")

    def _setup_room_handlers(self) -> None:
        """Set up handlers for room events."""
        if self._room is None:
            return

        try:
            # LiveKit room events
            self._room.on("track_subscribed", self._on_track_subscribed)
            self._room.on("track_unsubscribed", self._on_track_unsubscribed)
            self._room.on("participant_connected", self._on_participant_connected)
            self._room.on("participant_disconnected", self._on_participant_disconnected)
            self._room.on("reconnecting", self._on_reconnecting)
            self._room.on("reconnected", self._on_reconnected)
            self._room.on("disconnected", self._on_disconnected)
        except Exception as e:
            logger.warning(f"Failed to set up room handlers: {e}")

    async def _link_participants(self) -> None:
        """Link to participant(s) for audio I/O."""
        if self._room is None:
            return

        target = self._options.participant_identity

        try:
            # Get current participants
            participants = getattr(self._room, 'remote_participants', {})

            if target:
                # Wait for specific participant
                if target in participants:
                    await self._add_participant(participants[target])
                else:
                    logger.info(f"Waiting for participant: {target}")
                    # Will be handled by participant_connected event
            else:
                # Link to all non-agent participants
                for identity, participant in participants.items():
                    if not identity.startswith("agent_"):
                        await self._add_participant(participant)

        except Exception as e:
            logger.error(f"Error linking participants: {e}")

    async def _add_participant(self, participant: RemoteParticipant) -> None:
        """Add a participant to tracking."""
        identity = getattr(participant, 'identity', str(id(participant)))

        if identity in self._tracked_participants:
            logger.debug(f"Participant already tracked: {identity}")
            return

        tracked = TrackedParticipant(
            identity=identity,
            participant=participant,
        )
        self._tracked_participants[identity] = tracked

        # Set primary if not set
        if self._primary_participant is None:
            self._primary_participant = identity
            logger.info(f"Primary participant set: {identity}")

        # Subscribe to audio track
        await self._subscribe_to_audio(tracked)

    async def _subscribe_to_audio(self, tracked: TrackedParticipant) -> None:
        """Subscribe to participant's audio track."""
        if tracked.participant is None:
            return

        try:
            publications = getattr(tracked.participant, 'track_publications', {})

            for pub in publications.values():
                # Check if it's an audio track
                kind = getattr(pub, 'kind', None)
                if kind and str(kind).lower().find('audio') >= 0:
                    # Subscribe if not already
                    if not getattr(pub, 'subscribed', False):
                        if hasattr(pub, 'set_subscribed'):
                            pub.set_subscribed(True)

                    # Get track
                    track = getattr(pub, 'track', None)
                    if track:
                        await self._handle_track_subscribed(
                            track, pub, tracked.participant
                        )
                    break

        except Exception as e:
            logger.error(f"Error subscribing to audio for {tracked.identity}: {e}")
            tracked.track_state = TrackState.ERROR
            self._metrics["track_errors"] += 1

    async def _handle_track_subscribed(
        self,
        track: RemoteTrack,
        publication: Any,
        participant: RemoteParticipant,
    ) -> None:
        """Handle successful track subscription."""
        identity = getattr(participant, 'identity', 'unknown')
        tracked = self._tracked_participants.get(identity)

        if tracked is None:
            logger.warning(f"Track subscribed for unknown participant: {identity}")
            return

        # Cancel existing read task if any (handles reconnect)
        if tracked.read_task and not tracked.read_task.done():
            tracked.read_task.cancel()
            try:
                await tracked.read_task
            except asyncio.CancelledError:
                pass

        # Update state
        tracked.audio_track = track
        tracked.track_state = TrackState.SUBSCRIBED
        tracked.subscribed_at = time.time()

        # Start reading audio
        tracked.read_task = asyncio.create_task(
            self._read_audio_loop(tracked)
        )
        self._tasks.add(tracked.read_task)
        tracked.read_task.add_done_callback(self._tasks.discard)

        logger.info(f"Subscribed to audio track for: {identity}")

    async def _read_audio_loop(self, tracked: TrackedParticipant) -> None:
        """Read audio frames from track with normalization."""
        identity = tracked.identity
        logger.debug(f"Starting audio read for: {identity}")

        try:
            if LIVEKIT_AVAILABLE and tracked.audio_track is not None:
                # Use real LiveKit AudioStream
                audio_stream = rtc.AudioStream(
                    tracked.audio_track,
                    sample_rate=self._options.audio_input.sample_rate,
                    num_channels=self._options.audio_input.num_channels,
                )

                async for frame_event in audio_stream:
                    if not self._running or tracked.track_state != TrackState.SUBSCRIBED:
                        break

                    frame = frame_event.frame
                    # Convert LiveKit AudioFrame to bytes
                    audio_data = bytes(frame.data)

                    await self._process_incoming_frame(
                        audio_data,
                        frame.sample_rate,
                        frame.num_channels,
                        frame.samples_per_channel,
                        identity,
                    )

                await audio_stream.aclose()
            else:
                # Fallback for testing without LiveKit
                frame_duration_ms = self._options.audio_input.frame_duration_ms
                samples_per_frame = int(
                    self._options.audio_input.sample_rate * frame_duration_ms / 1000
                )

                while self._running and tracked.track_state == TrackState.SUBSCRIBED:
                    await asyncio.sleep(frame_duration_ms / 1000)
                    audio_data = b'\x00\x00' * samples_per_frame

                    await self._process_incoming_frame(
                        audio_data,
                        self._options.audio_input.sample_rate,
                        1,
                        samples_per_frame,
                        identity,
                    )

        except asyncio.CancelledError:
            logger.debug(f"Audio read cancelled for: {identity}")
        except Exception as e:
            logger.error(f"Error reading audio from {identity}: {e}")
            tracked.track_state = TrackState.ERROR
            self._metrics["track_errors"] += 1

    async def _process_incoming_frame(
        self,
        audio_data: bytes,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int,
        participant_identity: str,
    ) -> None:
        """Process and normalize incoming audio frame."""
        tracked = self._tracked_participants.get(participant_identity)
        if tracked:
            tracked.frames_received += 1
            tracked.last_audio_at = time.time()

        self._metrics["frames_received"] += 1

        # Normalize audio to canonical format
        if self._options.audio_input.auto_resample:
            normalized_frames = self._input_normalizer.normalize(
                audio_data,
                input_sample_rate=sample_rate,
                input_channels=num_channels,
            )
        else:
            normalized_frames = [audio_data]

        # Create events and queue them
        for frame_data in normalized_frames:
            event = AudioFrameIn(
                agent_id=self._agent_id,
                data=frame_data,
                sample_rate=self._options.audio_input.sample_rate,
                num_channels=self._options.audio_input.num_channels,
                samples_per_channel=len(frame_data) // 2,
                participant_identity=participant_identity,
            )

            # Queue with backpressure
            added = await self.audio_in_queue.put(event)

            # Also emit to event bus
            if added:
                await self._event_bus.emit(event)

    async def _publish_audio_loop(self) -> None:
        """Publish audio frames to room."""
        logger.debug("Starting audio publish loop")

        # Create audio source and track
        await self._setup_publish_track()

        while self._running:
            try:
                audio_event = await self.audio_out_queue.get()

                if audio_event is None:
                    continue

                # Publish to track
                await self._publish_frame(audio_event)
                self._metrics["frames_published"] += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in publish loop: {e}")

    async def _setup_publish_track(self) -> None:
        """Set up audio track for publishing."""
        if self._room is None:
            return

        try:
            if LIVEKIT_AVAILABLE:
                # Create AudioSource for publishing
                self._audio_source = rtc.AudioSource(
                    sample_rate=self._options.audio_output.sample_rate,
                    num_channels=self._options.audio_output.num_channels,
                )

                # Create local audio track
                self._published_track = rtc.LocalAudioTrack.create_audio_track(
                    self._options.audio_output.track_name,
                    self._audio_source,
                )

                # Publish to room
                options = rtc.TrackPublishOptions()
                options.source = rtc.TrackSource.SOURCE_MICROPHONE

                await self._room.local_participant.publish_track(
                    self._published_track,
                    options,
                )

                logger.info("Audio publish track ready (LiveKit)")
            else:
                logger.info("Audio publish track ready (mock - no LiveKit)")

        except Exception as e:
            logger.error(f"Failed to set up publish track: {e}")

    async def _publish_frame(self, audio: AudioFrameOut) -> None:
        """Publish a single audio frame."""
        if self._audio_source is None:
            return

        try:
            if LIVEKIT_AVAILABLE:
                # Create AudioFrame from our data
                frame = rtc.AudioFrame.create(
                    sample_rate=audio.sample_rate,
                    num_channels=audio.num_channels,
                    samples_per_channel=audio.samples_per_channel,
                )
                # Copy audio data into frame
                frame.data[:] = audio.data

                # Capture frame to audio source
                await self._audio_source.capture_frame(frame)

        except Exception as e:
            logger.error(f"Failed to publish frame: {e}")

    async def _unpublish_track(self) -> None:
        """Unpublish audio track."""
        if self._published_track is None:
            return

        try:
            if LIVEKIT_AVAILABLE and self._room is not None:
                await self._room.local_participant.unpublish_track(
                    self._published_track
                )

            if self._audio_source is not None:
                await self._audio_source.aclose()

        except Exception as e:
            logger.warning(f"Error unpublishing track: {e}")

        self._published_track = None
        self._audio_source = None

    async def _cleanup_participant(self, tracked: TrackedParticipant) -> None:
        """Clean up resources for a participant."""
        if tracked.read_task and not tracked.read_task.done():
            tracked.read_task.cancel()
            try:
                await tracked.read_task
            except asyncio.CancelledError:
                pass

        tracked.track_state = TrackState.UNSUBSCRIBED
        tracked.audio_track = None

    # Event handlers

    async def _on_track_subscribed(
        self,
        track: RemoteTrack,
        publication: Any,
        participant: RemoteParticipant,
    ) -> None:
        """Handle track subscribed event."""
        kind = getattr(track, 'kind', None)
        if kind and str(kind).lower().find('audio') < 0:
            return  # Not an audio track

        await self._handle_track_subscribed(track, publication, participant)

    async def _on_track_unsubscribed(
        self,
        track: RemoteTrack,
        publication: Any,
        participant: RemoteParticipant,
    ) -> None:
        """Handle track unsubscribed event."""
        identity = getattr(participant, 'identity', 'unknown')
        tracked = self._tracked_participants.get(identity)

        if tracked and tracked.audio_track == track:
            logger.info(f"Audio track unsubscribed for: {identity}")
            await self._cleanup_participant(tracked)

    async def _on_participant_connected(self, participant: RemoteParticipant) -> None:
        """Handle new participant connection."""
        identity = getattr(participant, 'identity', 'unknown')

        # Check if this is the participant we're waiting for
        target = self._options.participant_identity
        if target and identity != target:
            return

        # Don't add agents
        if identity.startswith("agent_"):
            return

        logger.info(f"Participant connected: {identity}")
        await self._add_participant(participant)

    async def _on_participant_disconnected(self, participant: RemoteParticipant) -> None:
        """Handle participant disconnection."""
        identity = getattr(participant, 'identity', 'unknown')
        tracked = self._tracked_participants.get(identity)

        if tracked:
            logger.info(f"Participant disconnected: {identity}")
            await self._cleanup_participant(tracked)
            del self._tracked_participants[identity]

            # Update primary if needed
            if self._primary_participant == identity:
                self._primary_participant = None
                if self._tracked_participants:
                    self._primary_participant = next(iter(self._tracked_participants))

            # Close session if configured
            if self._options.close_on_participant_leave and not self._tracked_participants:
                logger.info("All participants left, closing session")
                await self.stop()

    async def _on_reconnecting(self) -> None:
        """Handle reconnection attempt."""
        logger.info("Room reconnecting...")
        self._metrics["reconnects"] += 1

    async def _on_reconnected(self) -> None:
        """Handle successful reconnection."""
        logger.info("Room reconnected")
        # Re-subscribe to tracks
        for tracked in self._tracked_participants.values():
            if tracked.track_state != TrackState.SUBSCRIBED:
                await self._subscribe_to_audio(tracked)

    async def _on_disconnected(self) -> None:
        """Handle room disconnection."""
        logger.warning("Room disconnected")
        if not self._options.reconnect_on_disconnect:
            await self.stop()

    async def _log_stats_loop(self) -> None:
        """Periodically log audio statistics."""
        interval = self._options.audio_input.stats_interval_seconds

        while self._running:
            await asyncio.sleep(interval)

            if not self._running:
                break

            stats = {
                "in_queue": self.audio_in_queue.size,
                "out_queue": self.audio_out_queue.size,
                "frames_in": self._metrics["frames_received"],
                "frames_out": self._metrics["frames_published"],
                "participants": len(self._tracked_participants),
                "in_drops": self.audio_in_queue.metrics.items_dropped,
                "out_drops": self.audio_out_queue.metrics.items_dropped,
            }
            logger.info(f"RoomAdapter stats: {stats}")

    # Public API

    async def publish_audio(self, audio: AudioFrameOut) -> bool:
        """Queue audio for publishing to room."""
        return await self.audio_out_queue.put(audio)

    @property
    def primary_participant_identity(self) -> Optional[str]:
        """Get the primary participant identity."""
        return self._primary_participant

    @property
    def is_running(self) -> bool:
        """Check if adapter is running."""
        return self._running

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get adapter metrics."""
        return {
            **self._metrics,
            "audio_in_queue_metrics": self.audio_in_queue.metrics.to_dict(),
            "audio_out_queue_metrics": self.audio_out_queue.metrics.to_dict(),
            "normalizer_metrics": self._input_normalizer.metrics,
            "tracked_participants": list(self._tracked_participants.keys()),
        }


class MockRoomAdapter(RoomAdapter):
    """
    Mock room adapter for testing without LiveKit.

    Provides audio I/O via local injection instead of network.
    """

    def __init__(
        self,
        event_bus: EventBus,
        options: Optional[RoomOptions] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(None, event_bus, options, agent_id)

        # Queue for injected audio
        self._injected_audio: asyncio.Queue[bytes] = asyncio.Queue()
        self._mock_read_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start mock adapter."""
        if self._running:
            return

        logger.info(f"MockRoomAdapter starting for agent {self._agent_id}")
        self._running = True

        # Start mock audio read
        self._mock_read_task = asyncio.create_task(self._mock_read_audio_loop())
        self._tasks.add(self._mock_read_task)

        # Start publish loop
        publish_task = asyncio.create_task(self._publish_audio_loop())
        self._tasks.add(publish_task)

        logger.info(f"MockRoomAdapter started for agent {self._agent_id}")

    async def inject_audio(
        self,
        audio_data: bytes,
        sample_rate: int = CANONICAL_FORMAT.sample_rate,
    ) -> None:
        """Inject audio data as if from user (for testing)."""
        await self._injected_audio.put((audio_data, sample_rate))

    async def _mock_read_audio_loop(self) -> None:
        """Read from injection queue or generate silence."""
        frame_duration_ms = self._options.audio_input.frame_duration_ms
        samples_per_frame = int(
            self._options.audio_input.sample_rate * frame_duration_ms / 1000
        )

        while self._running:
            try:
                # Check for injected audio
                try:
                    audio_data, sample_rate = await asyncio.wait_for(
                        self._injected_audio.get(),
                        timeout=frame_duration_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    # Generate silence
                    audio_data = b'\x00\x00' * samples_per_frame
                    sample_rate = self._options.audio_input.sample_rate

                await self._process_incoming_frame(
                    audio_data,
                    sample_rate,
                    1,  # mono
                    len(audio_data) // 2,
                    "mock_user",
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in mock read loop: {e}")

    def _setup_room_handlers(self) -> None:
        """No handlers needed for mock."""
        pass

    async def _link_participants(self) -> None:
        """No participants for mock."""
        pass

    async def _setup_publish_track(self) -> None:
        """No track setup for mock."""
        pass
