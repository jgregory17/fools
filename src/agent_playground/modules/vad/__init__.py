"""Voice Activity Detection modules."""

from .interface import VADBackend, VADResult
from .energy_vad import EnergyVAD
from .silero_vad import SileroVAD

__all__ = [
    "VADBackend",
    "VADResult",
    "EnergyVAD",
    "SileroVAD",
]
