from collections.abc import Callable
from typing import Any


class UnsupportedSpeechProvider(ValueError):
    """Raised when configuration names a provider that is not installed."""


class SpeechProviderRegistry:
    """Small composition-root registry; application code stays provider agnostic."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Any]] = {}

    def register(self, provider: str, factory: Callable[[], Any]) -> None:
        self._factories[provider.strip().lower()] = factory

    def create(self, provider: str) -> Any:
        try:
            return self._factories[provider.strip().lower()]()
        except KeyError as exc:
            raise UnsupportedSpeechProvider(provider) from exc
