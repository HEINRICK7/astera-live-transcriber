#!/usr/bin/env python3
"""Small repeatable benchmark for local Parakeet inference."""

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path

from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.infrastructure.engines.runtime import build_engine_runtime


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16_000:
            raise ValueError(f"{path} must be PCM16 mono 16kHz")
        data = wav.readframes(wav.getnframes())
    return data, round(len(data) / 2 / 16_000 * 1000)


async def run(paths: list[Path], model_path: str) -> None:
    settings = Settings(engine="parakeet", model_path=model_path)
    runtime = build_engine_runtime(settings)
    await runtime.start()
    rows = []
    try:
        for path in paths:
            audio, duration_ms = read_wav(path)
            started = time.perf_counter()
            result = await runtime.engine.transcribe(audio, language="pt-BR")
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            rows.append(
                {
                    "file": str(path),
                    "audio_ms": duration_ms,
                    "inference_ms": elapsed_ms,
                    "realtime_factor": round(elapsed_ms / max(duration_ms, 1), 4),
                    "text": result.text,
                }
            )
    finally:
        await runtime.close()
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--model-path", default="models/parakeet-tdt-0.6b-v3-int8")
    args = parser.parse_args()
    asyncio.run(run(args.files, args.model_path))


if __name__ == "__main__":
    main()
