"""
Resource Manager for GPU and Device Coordination.

Manages device placement for ML models to prevent:
- GPU memory contention (OOM errors)
- Resource conflicts between concurrent models
- Inefficient device utilization

Design Goals:
- Centralized device assignment
- Memory tracking (where available)
- Serialization of GPU-heavy operations (optional)
- Clear logging of resource allocation
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional, Dict
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """Supported device types."""
    CPU = auto()
    CUDA = auto()
    MPS = auto()   # Apple Silicon
    AUTO = auto()  # Auto-detect best device


@dataclass
class DeviceInfo:
    """Information about a compute device."""
    device_type: DeviceType
    device_id: int = 0
    name: str = ""
    total_memory_mb: int = 0
    available_memory_mb: int = 0

    @property
    def device_string(self) -> str:
        """Get device string for frameworks (e.g., 'cuda:0', 'cpu')."""
        if self.device_type == DeviceType.CPU:
            return "cpu"
        elif self.device_type == DeviceType.CUDA:
            return f"cuda:{self.device_id}"
        elif self.device_type == DeviceType.MPS:
            return "mps"
        return "cpu"


@dataclass
class ResourceAllocation:
    """Tracks a resource allocation."""
    module_name: str
    device: DeviceInfo
    estimated_memory_mb: int = 0
    allocated_at: float = 0.0


@dataclass
class ResourceManagerConfig:
    """Configuration for the resource manager."""

    # Default device assignments by module type
    # Allows distributing load across devices
    device_assignments: Dict[str, str] = field(default_factory=lambda: {
        "asr": "auto",      # ASR often needs GPU for speed
        "llm": "auto",      # LLM typically on GPU
        "tts": "cpu",       # TTS often fast enough on CPU
        "vad": "cpu",       # VAD is lightweight
        "memory": "cpu",    # Memory/embedding operations
    })

    # Memory limits per device (MB)
    # 0 = no limit
    memory_limits: Dict[str, int] = field(default_factory=dict)

    # Serialize GPU operations to prevent memory spikes
    serialize_gpu_ops: bool = False

    # Log resource allocation details
    verbose_logging: bool = False

    # Fallback device if requested device unavailable
    fallback_device: str = "cpu"


class ResourceManager:
    """
    Manages compute resources across the pipeline.

    Provides centralized device assignment and optional memory tracking
    to prevent OOM errors and resource conflicts.

    Usage:
        manager = ResourceManager()

        # Get device for a module
        device = manager.get_device("asr")
        model = load_model(device=device.device_string)

        # Track memory (optional)
        manager.register_allocation("asr", device, estimated_mb=500)

        # Serialize GPU operations (optional)
        async with manager.gpu_lock():
            result = await gpu_heavy_operation()
    """

    def __init__(self, config: Optional[ResourceManagerConfig] = None):
        self.config = config or ResourceManagerConfig()

        # Discovered devices
        self._devices: Dict[str, DeviceInfo] = {}
        self._allocations: Dict[str, ResourceAllocation] = {}

        # GPU serialization lock
        self._gpu_lock = asyncio.Lock()

        # Initialize device discovery
        self._discover_devices()

    def _discover_devices(self) -> None:
        """Discover available compute devices."""
        # CPU is always available
        self._devices["cpu"] = DeviceInfo(
            device_type=DeviceType.CPU,
            name="CPU",
        )

        # Check for CUDA
        cuda_available = self._check_cuda()
        if cuda_available:
            cuda_devices = self._enumerate_cuda_devices()
            for i, device in enumerate(cuda_devices):
                self._devices[f"cuda:{i}"] = device
            if cuda_devices:
                self._devices["cuda"] = cuda_devices[0]  # Default CUDA device

        # Check for MPS (Apple Silicon)
        mps_available = self._check_mps()
        if mps_available:
            self._devices["mps"] = DeviceInfo(
                device_type=DeviceType.MPS,
                name="Apple Silicon GPU",
            )

        logger.info(f"ResourceManager: discovered devices: {list(self._devices.keys())}")

    def _check_cuda(self) -> bool:
        """Check if CUDA is available."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            pass

        # Check environment variable
        if os.environ.get("CUDA_VISIBLE_DEVICES"):
            return True

        return False

    def _enumerate_cuda_devices(self) -> list[DeviceInfo]:
        """Enumerate CUDA devices with memory info."""
        devices = []

        try:
            import torch

            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                total_mem = props.total_memory // (1024 * 1024)

                # Get available memory
                try:
                    torch.cuda.set_device(i)
                    free_mem = torch.cuda.mem_get_info()[0] // (1024 * 1024)
                except Exception:
                    free_mem = total_mem

                devices.append(DeviceInfo(
                    device_type=DeviceType.CUDA,
                    device_id=i,
                    name=props.name,
                    total_memory_mb=total_mem,
                    available_memory_mb=free_mem,
                ))

        except ImportError:
            # No torch, try nvidia-smi
            try:
                import subprocess
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    for i, line in enumerate(result.stdout.strip().split('\n')):
                        parts = line.split(',')
                        if len(parts) >= 3:
                            devices.append(DeviceInfo(
                                device_type=DeviceType.CUDA,
                                device_id=i,
                                name=parts[0].strip(),
                                total_memory_mb=int(parts[1].strip()),
                                available_memory_mb=int(parts[2].strip()),
                            ))
            except Exception:
                pass

        return devices

    def _check_mps(self) -> bool:
        """Check if MPS (Apple Silicon) is available."""
        try:
            import torch
            return torch.backends.mps.is_available()
        except (ImportError, AttributeError):
            return False

    def get_device(self, module_name: str) -> DeviceInfo:
        """
        Get the assigned device for a module.

        Args:
            module_name: Name of the module (e.g., "asr", "llm", "tts")

        Returns:
            DeviceInfo for the assigned device
        """
        # Check configured assignment
        device_str = self.config.device_assignments.get(
            module_name,
            self.config.fallback_device
        )

        # Handle "auto" assignment
        if device_str == "auto":
            device_str = self._auto_select_device(module_name)

        # Look up device
        if device_str in self._devices:
            device = self._devices[device_str]
        else:
            logger.warning(
                f"Device '{device_str}' not found for {module_name}, "
                f"using {self.config.fallback_device}"
            )
            device = self._devices.get(
                self.config.fallback_device,
                self._devices["cpu"]
            )

        if self.config.verbose_logging:
            logger.info(f"ResourceManager: {module_name} -> {device.device_string}")

        return device

    def _auto_select_device(self, module_name: str) -> str:
        """Auto-select best device for a module."""
        # Prefer CUDA if available
        if "cuda" in self._devices:
            return "cuda"

        # Then MPS
        if "mps" in self._devices:
            return "mps"

        # Fallback to CPU
        return "cpu"

    def register_allocation(
        self,
        module_name: str,
        device: DeviceInfo,
        estimated_mb: int = 0,
    ) -> None:
        """Register a resource allocation for tracking."""
        import time

        self._allocations[module_name] = ResourceAllocation(
            module_name=module_name,
            device=device,
            estimated_memory_mb=estimated_mb,
            allocated_at=time.time(),
        )

        if self.config.verbose_logging:
            logger.info(
                f"ResourceManager: registered {module_name} on {device.device_string} "
                f"(~{estimated_mb}MB)"
            )

    def release_allocation(self, module_name: str) -> None:
        """Release a resource allocation."""
        if module_name in self._allocations:
            del self._allocations[module_name]

            if self.config.verbose_logging:
                logger.info(f"ResourceManager: released {module_name}")

    @asynccontextmanager
    async def gpu_lock(self):
        """
        Acquire lock for GPU-heavy operations.

        Use this to serialize operations that might cause memory spikes.
        """
        if self.config.serialize_gpu_ops:
            async with self._gpu_lock:
                yield
        else:
            yield

    def get_memory_info(self, device_str: str = "cuda") -> Optional[Dict[str, int]]:
        """
        Get current memory info for a device.

        Returns:
            Dict with 'total_mb', 'used_mb', 'free_mb' or None if unavailable
        """
        if device_str not in self._devices:
            return None

        device = self._devices[device_str]

        if device.device_type != DeviceType.CUDA:
            return None

        try:
            import torch

            if not torch.cuda.is_available():
                return None

            torch.cuda.set_device(device.device_id)
            free, total = torch.cuda.mem_get_info()

            return {
                "total_mb": total // (1024 * 1024),
                "used_mb": (total - free) // (1024 * 1024),
                "free_mb": free // (1024 * 1024),
            }

        except Exception:
            return None

    @property
    def available_devices(self) -> list[str]:
        """List of available device strings."""
        return list(self._devices.keys())

    @property
    def allocations(self) -> Dict[str, ResourceAllocation]:
        """Current resource allocations."""
        return self._allocations.copy()

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get resource manager metrics."""
        metrics = {
            "available_devices": self.available_devices,
            "allocations": {
                name: {
                    "device": alloc.device.device_string,
                    "estimated_mb": alloc.estimated_memory_mb,
                }
                for name, alloc in self._allocations.items()
            },
        }

        # Add memory info for CUDA devices
        for device_str in self._devices:
            if "cuda" in device_str:
                mem_info = self.get_memory_info(device_str)
                if mem_info:
                    metrics[f"{device_str}_memory"] = mem_info

        return metrics


# Global resource manager instance (optional singleton pattern)
_global_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """Get or create the global resource manager."""
    global _global_manager
    if _global_manager is None:
        _global_manager = ResourceManager()
    return _global_manager


def set_resource_manager(manager: ResourceManager) -> None:
    """Set the global resource manager."""
    global _global_manager
    _global_manager = manager
