"""Voice activity detection adapters."""

from .passthrough import PassthroughVad
from .rms import RmsVad

__all__ = ["PassthroughVad", "RmsVad"]
