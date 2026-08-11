from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ParakeetConfig:
    model_path: Path
    sample_rate: int = 16_000
    num_threads: int = 4
    provider: str = "cpu"
    debug: bool = False
    warmup: bool = True
    max_concurrent_inferences: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.num_threads <= 0:
            raise ValueError("num_threads must be positive")
        if self.max_concurrent_inferences <= 0:
            raise ValueError("max_concurrent_inferences must be positive")
