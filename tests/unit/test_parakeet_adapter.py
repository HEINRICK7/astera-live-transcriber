from pathlib import Path

import pytest

from astera_live_transcriber.infrastructure.engines.parakeet.config import ParakeetConfig
from astera_live_transcriber.infrastructure.engines.parakeet.loader import ParakeetModelLoader
from astera_live_transcriber.infrastructure.engines.parakeet.mapper import map_language, map_result


def test_language_mapping_preserves_external_language() -> None:
    assert map_language("pt-BR") == "pt"
    assert map_language("pt") == "pt"


def test_result_mapper_trims_text_and_converts_timestamps() -> None:
    result = map_result(
        {
            "text": "  olá mundo  ",
            "confidence": 0.91,
            "words": [{"word": "olá", "start": 0.1, "end": 0.4}],
        },
        "pt-BR",
        700,
    )
    assert result.text == "olá mundo"
    assert result.language == "pt-BR"
    assert result.words[0].start_ms == 100
    assert result.words[0].end_ms == 400


def test_loader_fails_explicitly_for_incomplete_model(tmp_path: Path) -> None:
    config = ParakeetConfig(model_path=tmp_path)
    with pytest.raises(Exception, match="missing"):
        ParakeetModelLoader(config).load()
