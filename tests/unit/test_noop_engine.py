import pytest

from astera_live_transcriber.infrastructure.engines.noop.adapter import NoopTranscriptionEngine


@pytest.mark.asyncio
async def test_noop_engine_is_explicitly_empty_until_real_engine_phase() -> None:
    result = await NoopTranscriptionEngine().transcribe(b"audio", language="pt-BR")

    assert result.text == ""
    assert result.language == "pt-BR"
    assert result.segments == ()

