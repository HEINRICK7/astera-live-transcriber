class SpeechEngineError(RuntimeError):
    """Base error exposed by any live speech engine."""


class SpeechAuthenticationError(SpeechEngineError):
    """Provider credentials were rejected."""


class SpeechRateLimitError(SpeechEngineError):
    """The provider refused the request because of rate limiting."""


class SpeechConnectionError(SpeechEngineError):
    """The provider connection failed or was unexpectedly closed."""


class SpeechProviderUnavailableError(SpeechEngineError):
    """The provider is temporarily unavailable."""


class SpeechConfigurationError(SpeechEngineError):
    """The provider cannot support the requested session configuration."""


class SpeechBackpressureError(SpeechEngineError):
    """The bounded audio queue cannot accept more audio."""
