class ParakeetEngineError(RuntimeError):
    """A model inference failed after the engine was loaded."""


class ParakeetModelError(ParakeetEngineError):
    """The configured model directory is missing or incomplete."""
