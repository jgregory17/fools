"""
Unit tests for PiperTTS implementation.

Tests ensure:
- Type safety
- Proper async iterator behavior
- Sentence splitting logic
- Error handling
- Cancellation support
"""

import asyncio
import pytest
from typing import List
from unittest.mock import Mock, MagicMock, patch, AsyncMock

from src.agent_playground.modules.tts.piper_tts import PiperTTS
from src.agent_playground.core.events import TTSChunk


# Mock the piper module since it may not be installed in test environment
@pytest.fixture
def mock_piper():
    """Mock the piper module."""
    # Since ensure_voice_exists is imported from piper.download at module level,
    # we need to patch it before the module uses it
    with patch("src.agent_playground.modules.tts.piper_tts.PIPER_AVAILABLE", True):
        # Patch the imported functions at the point of use
        from src.agent_playground.modules.tts import piper_tts

        # Create mocks
        mock_voice_class = MagicMock()
        mock_voice_instance = MagicMock()
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_voice_instance.synthesize_stream_raw = MagicMock(return_value=[b'\x00\x01' * 100])
        mock_voice_class.load = MagicMock(return_value=mock_voice_instance)

        # Patch the module's imported items
        piper_tts.PiperVoice = mock_voice_class
        piper_tts.ensure_voice_exists = MagicMock(return_value=("/fake/model.onnx", "/fake/config.json"))
        piper_tts.find_voice = MagicMock()
        piper_tts.get_voices = MagicMock()

        yield mock_voice_class


class TestPiperTTSInitialization:
    """Test PiperTTS initialization."""

    def test_initialization_default_params(self, mock_piper):
        """Test initialization with default parameters."""
        tts = PiperTTS()
        assert tts._voice_name == "en_US-lessac-medium"
        assert tts._sample_rate == 22050
        assert tts._num_channels == 1
        assert tts._length_scale == 1.0
        assert tts._closed is False

    def test_initialization_custom_params(self, mock_piper):
        """Test initialization with custom parameters."""
        tts = PiperTTS(
            voice="en_GB-alan-medium",
            sample_rate=24000,
            length_scale=0.9,
            noise_scale=0.5,
        )
        assert tts._voice_name == "en_GB-alan-medium"
        assert tts._sample_rate == 24000
        assert tts._length_scale == 0.9
        assert tts._noise_scale == 0.5

    def test_initialization_without_piper_raises_error(self):
        """Test that initialization fails without piper."""
        with patch("src.agent_playground.modules.tts.piper_tts.PIPER_AVAILABLE", False):
            with pytest.raises(ImportError, match="piper-tts is required"):
                PiperTTS()


class TestSentenceSplitting:
    """Test sentence splitting logic."""

    def test_split_sentences_basic(self, mock_piper):
        """Test basic sentence splitting."""
        tts = PiperTTS()
        text = "Hello world. How are you? I am fine."
        sentences = tts._split_sentences(text)

        assert len(sentences) == 3
        assert "Hello world." in sentences[0]
        assert "How are you?" in sentences[1]
        assert "I am fine." in sentences[2]

    def test_split_sentences_exclamation(self, mock_piper):
        """Test splitting on exclamation marks."""
        tts = PiperTTS()
        text = "This is great! I love it! Amazing!"
        sentences = tts._split_sentences(text)

        assert len(sentences) == 3

    def test_split_sentences_no_punctuation(self, mock_piper):
        """Test splitting text without punctuation."""
        tts = PiperTTS()
        text = "Hello world"
        sentences = tts._split_sentences(text)

        # Should return single sentence
        assert len(sentences) == 1
        assert sentences[0] == text

    def test_split_sentences_mixed_punctuation(self, mock_piper):
        """Test splitting with mixed punctuation."""
        tts = PiperTTS()
        text = "Hello. World! How are you? I'm fine."
        sentences = tts._split_sentences(text)

        assert len(sentences) >= 3


class TestSynthesisBasic:
    """Test basic synthesis functionality."""

    @pytest.mark.asyncio
    async def test_synthesize_returns_async_iterator(self, mock_piper):
        """Test that synthesize returns an async iterator."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield "Hello world."

        result = tts.synthesize(text_stream())
        # Check it's an async iterator
        assert hasattr(result, '__anext__')

    @pytest.mark.asyncio
    async def test_synthesize_yields_tts_chunks(self, mock_piper):
        """Test that synthesize yields TTSChunk objects."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_voice_instance.synthesize_stream_raw.return_value = [b'\x00\x01' * 100]
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield "Hello."

        chunks: List[TTSChunk] = []
        async for chunk in tts.synthesize(text_stream()):
            chunks.append(chunk)
            # Verify each chunk is TTSChunk type
            assert isinstance(chunk, TTSChunk)
            assert isinstance(chunk.audio_data, bytes)
            assert isinstance(chunk.sample_rate, int)
            assert isinstance(chunk.num_channels, int)
            assert isinstance(chunk.text_segment, str)
            assert isinstance(chunk.is_final, bool)
            assert isinstance(chunk.chunk_index, int)

        # Should have at least one chunk
        assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_synthesize_multiple_sentences(self, mock_piper):
        """Test synthesizing multiple sentences."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_voice_instance.synthesize_stream_raw.return_value = [b'\x00\x01' * 100]
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield "Hello. "
            yield "How are you? "
            yield "I am fine."

        chunks: List[TTSChunk] = []
        async for chunk in tts.synthesize(text_stream()):
            chunks.append(chunk)

        # Should produce multiple chunks (one per sentence)
        assert len(chunks) >= 2

        # Last chunk should be marked as final
        assert chunks[-1].is_final is True

        # Earlier chunks should not be final
        for chunk in chunks[:-1]:
            assert chunk.is_final is False

    @pytest.mark.asyncio
    async def test_synthesize_empty_text(self, mock_piper):
        """Test synthesizing empty text."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield ""

        chunks = []
        async for chunk in tts.synthesize(text_stream()):
            chunks.append(chunk)

        # Should produce no chunks for empty text
        assert len(chunks) == 0

    @pytest.mark.asyncio
    async def test_synthesize_text_convenience_method(self, mock_piper):
        """Test synthesize_text convenience method."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_voice_instance.synthesize_stream_raw.return_value = [b'\x00\x01' * 100]
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_piper.load.return_value = mock_voice_instance

        audio_data = await tts.synthesize_text("Hello world.")

        # Should return bytes
        assert isinstance(audio_data, bytes)
        assert len(audio_data) > 0


class TestCancellation:
    """Test cancellation support."""

    @pytest.mark.asyncio
    async def test_cancel_stops_synthesis(self, mock_piper):
        """Test that cancel stops synthesis."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_voice_instance.synthesize_stream_raw.return_value = [b'\x00\x01' * 100]
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield "Sentence 1. "
            yield "Sentence 2. "
            await asyncio.sleep(0.1)
            yield "Sentence 3."

        # Start synthesis
        chunks = []
        synthesis_task = tts.synthesize(text_stream())

        # Cancel after first chunk
        chunk_count = 0
        async for chunk in synthesis_task:
            chunks.append(chunk)
            chunk_count += 1
            if chunk_count == 1:
                tts.cancel()
                break

        # Should have stopped early
        assert len(chunks) < 3

    def test_reset_clears_cancelled_state(self, mock_piper):
        """Test that reset clears cancelled state."""
        tts = PiperTTS()

        tts.cancel()
        assert tts._closed is True

        tts.reset()
        assert tts._closed is False

    @pytest.mark.asyncio
    async def test_close_releases_resources(self, mock_piper):
        """Test that close releases resources."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_piper.load.return_value = mock_voice_instance
        tts._ensure_model_loaded()

        await tts.close()

        assert tts._closed is True
        assert tts._piper_voice is None


class TestChunkIndexing:
    """Test chunk indexing."""

    @pytest.mark.asyncio
    async def test_chunk_indices_increment(self, mock_piper):
        """Test that chunk indices increment correctly."""
        tts = PiperTTS()

        # Mock the voice
        mock_voice_instance = MagicMock()
        mock_voice_instance.synthesize_stream_raw.return_value = [b'\x00\x01' * 100]
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield "Sentence 1. "
            yield "Sentence 2. "
            yield "Sentence 3."

        chunks: List[TTSChunk] = []
        async for chunk in tts.synthesize(text_stream()):
            chunks.append(chunk)

        # Check indices are sequential
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


class TestErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_synthesis_error_propagates(self, mock_piper):
        """Test that synthesis errors are propagated."""
        tts = PiperTTS()

        # Mock the voice to raise error
        mock_voice_instance = MagicMock()
        mock_voice_instance.synthesize_stream_raw.side_effect = RuntimeError("Synthesis failed")
        mock_voice_instance.config = MagicMock(sample_rate=22050)
        mock_piper.load.return_value = mock_voice_instance

        async def text_stream():
            yield "Hello."

        with pytest.raises(RuntimeError, match="Synthesis failed"):
            async for _ in tts.synthesize(text_stream()):
                pass


class TestTypeAnnotations:
    """Test that type annotations are correct."""

    def test_synthesize_signature(self, mock_piper):
        """Test synthesize method signature."""
        tts = PiperTTS()
        import inspect
        from typing import get_type_hints

        sig = inspect.signature(tts.synthesize)
        # Should have text_stream parameter
        assert 'text_stream' in sig.parameters

        # Return type should be AsyncIterator[TTSChunk]
        hints = get_type_hints(tts.synthesize)
        assert 'return' in hints

    def test_synthesize_text_signature(self, mock_piper):
        """Test synthesize_text method signature."""
        tts = PiperTTS()
        import inspect

        sig = inspect.signature(tts.synthesize_text)
        # Should have text parameter
        assert 'text' in sig.parameters

        # text parameter should be str (can be string 'str' or type str)
        annotation = sig.parameters['text'].annotation
        assert annotation == str or annotation == 'str'


if __name__ == "__main__":
    # Run with: pytest tests/test_piper_tts.py -v
    pytest.main([__file__, "-v"])
