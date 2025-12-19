#!/bin/bash
# Test Piper TTS inside Docker container

TEXT="${1:-Hello, this is a test of the text to speech system.}"
OUTPUT="${2:-test_tts_output.wav}"

echo "=========================================================="
echo "Testing Piper TTS"
echo "=========================================================="
echo ""
echo "Text: $TEXT"
echo "Output: $OUTPUT"
echo ""

# Find Piper in Docker container
echo "Looking for Piper..."
docker compose exec agent bash -c "which piper" 2>/dev/null || {
    echo "Error: Piper not found in container"
    echo "Checking Python package..."
    docker compose exec agent python -c "import piper; print('Piper Python package found')" 2>/dev/null || {
        echo "Error: Piper Python package not installed"
        exit 1
    }
}

# Try synthesizing with Piper
echo ""
echo "Synthesizing..."
docker compose exec agent bash -c "
echo '$TEXT' | piper \
    --model /app/voices/en_US-lessac-medium.onnx \
    --output_file /app/$OUTPUT \
    2>&1
"

# Check if file was created
if docker compose exec agent test -f "/app/$OUTPUT"; then
    echo ""
    echo "✓ SUCCESS: Audio synthesized at /app/$OUTPUT in container"
    echo ""
    echo "Copy to local:"
    echo "  docker cp fools-agent-1:/app/$OUTPUT ./"
    echo ""
    echo "Test transcription:"
    echo "  python examples/test_vosk_simple.py $OUTPUT"
    exit 0
else
    echo ""
    echo "✗ FAILED: Audio file not created"
    exit 1
fi
