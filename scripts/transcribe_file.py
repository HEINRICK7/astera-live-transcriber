#!/usr/bin/env python3
"""Upload one audio file and print its live transcription events."""

import argparse
import asyncio
import json
import mimetypes
from pathlib import Path

import httpx
import websockets


async def transcribe(path: Path, server: str, mode: str) -> None:
    if not path.is_file():
        raise SystemExit(f"arquivo não encontrado: {path}")
    server = server.rstrip("/")
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            health = await client.get(f"{server}/health")
            health.raise_for_status()
            status = await client.get(f"{server}/v1/engine/status")
            status.raise_for_status()
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"não foi possível conectar ao servidor {server}; inicie o uvicorn primeiro"
            ) from exc

        engine_status = status.json()
        if engine_status.get("status") != "ready":
            details = json.dumps(engine_status, ensure_ascii=False)
            raise SystemExit(f"engine não está pronta: {details}")
        if engine_status.get("engine") == "noop":
            raise SystemExit(
                "o servidor está usando noop; configure ASTERA_TRANSCRIBER_ENGINE=parakeet"
            )

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as audio:
            response = await client.post(
                f"{server}/v1/realtime/files",
                files={"file": (path.name, audio, mime_type)},
                data={"mode": mode, "language": "pt-BR"},
            )
        if response.status_code != 202:
            raise SystemExit(f"upload falhou ({response.status_code}): {response.text}")
        session = response.json()

    websocket_server = server.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    websocket_url = f"{websocket_server}{session['websocket']}"
    print(f"sessão: {session['session_id']}", flush=True)
    async with websockets.connect(websocket_url, max_size=None) as websocket:
        async for raw_event in websocket:
            event = json.loads(raw_event)
            event_type = event.get("type")
            if event_type in {"transcript.partial", "transcript.revised", "transcript.committed"}:
                print(f"[{event_type}] {event.get('text', '')}", flush=True)
            elif event_type == "error":
                print(json.dumps(event, ensure_ascii=False), flush=True)
                break
            elif event_type in {"audio.completed", "session.completed"}:
                print(f"[{event_type}]", flush=True)
            else:
                print(f"[{event_type}]", flush=True)
            if event_type == "session.completed":
                break


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcre um arquivo como stream live")
    parser.add_argument("audio", type=Path, help="caminho do arquivo MP3 ou WAV")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=("realtime", "accelerated"), default="realtime")
    args = parser.parse_args()
    asyncio.run(transcribe(args.audio, args.server, args.mode))


if __name__ == "__main__":
    main()
