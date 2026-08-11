import importlib
from typing import Any

from .config import ParakeetConfig
from .exceptions import ParakeetModelError

REQUIRED_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")


class ParakeetModelLoader:
    """Loads the resident sherpa-onnx recognizer exactly once."""

    def __init__(self, config: ParakeetConfig, sherpa_module: Any | None = None) -> None:
        self.config = config
        self._sherpa_module = sherpa_module
        self._recognizer: Any | None = None

    @property
    def recognizer(self) -> Any:
        if self._recognizer is None:
            raise RuntimeError("Parakeet model is not loaded")
        return self._recognizer

    def validate_model(self) -> None:
        if not self.config.model_path.is_dir():
            raise ParakeetModelError(
                f"Parakeet model directory not found: {self.config.model_path}"
            )
        missing = [name for name in REQUIRED_FILES if not (self.config.model_path / name).is_file()]
        if missing:
            raise ParakeetModelError(
                f"Parakeet model is incomplete; missing: {', '.join(missing)}"
            )

    def load(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        self.validate_model()
        sherpa = self._sherpa_module or importlib.import_module("sherpa_onnx")
        path = self.config.model_path
        try:
            self._recognizer = sherpa.OfflineRecognizer.from_transducer(
                encoder=str(path / "encoder.int8.onnx"),
                decoder=str(path / "decoder.int8.onnx"),
                joiner=str(path / "joiner.int8.onnx"),
                tokens=str(path / "tokens.txt"),
                num_threads=self.config.num_threads,
                sample_rate=self.config.sample_rate,
                feature_dim=80,
                decoding_method="greedy_search",
                debug=self.config.debug,
                provider=self.config.provider,
            )
            if self.config.warmup:
                self._warmup()
        except ParakeetModelError:
            raise
        except Exception as exc:
            self._recognizer = None
            raise ParakeetModelError(f"Unable to load Parakeet model: {exc}") from exc
        return self._recognizer

    def _warmup(self) -> None:
        import numpy as np

        stream = self.recognizer.create_stream()
        stream.accept_waveform(
            self.config.sample_rate,
            np.zeros(self.config.sample_rate // 10, dtype=np.float32),
        )
        self.recognizer.decode_stream(stream)
