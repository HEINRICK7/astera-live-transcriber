# Astera Live Transcriber

Serviço independente de transcrição estruturada para o Astera.

## Responsabilidade

```text
ÁUDIO → ASTERA LIVE TRANSCRIBER → TRANSCRIÇÃO ESTRUTURADA
```

Este serviço não interpreta medicina, não gera SOAP, hipóteses ou componentes A2UI. Ele entrega texto, segmentos, revisões, status e timestamps para que o Astera decida o significado clínico.

## Status

Bootstrap `v0.1.0` sem engine STT real. A aplicação já possui:

- `GET /health`
- `GET /v1/models`
- modelo de domínio `TranscriptSegment`
- contrato `TranscriptionEnginePort`
- Noop Engine para desenvolvimento
- base para HTTP transcription e WebSocket realtime
- pipeline realtime em memória com Ring Buffer, VAD RMS e turn detection temporal
- lifecycle `partial` → `revised` → `committed` com identidade estável de segmento
- sessões isoladas, backpressure limitada e disconnect com limpeza
- testes unitários e de integração
- Docker, GitHub Actions para CI e workflow manual de staging

## Desenvolvimento local

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
uvicorn astera_live_transcriber.main:app --reload
```

Endpoints do checkpoint:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/models
```

### Realtime

Conecte em `WS /v1/realtime/transcription`, envie `session.create` e depois eventos `audio.append` com áudio PCM16 mono em base64. O formato canônico desta fase é 16 kHz. O servidor emite `session.created`, `speech.started`, `speech.stop_candidate`, `transcript.partial`, `transcript.revised` e `transcript.committed`.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

## Staging na VPS

O workflow `Deploy staging` é manual (`workflow_dispatch`) e usa um projeto Docker separado em `/opt/astera-live-transcriber-staging`, na porta `48080`. Ele não reinicia nem altera os projetos existentes.

Antes de executá-lo, configure no ambiente `staging` do GitHub:

- `STAGING_HOST`
- `STAGING_USER`
- `STAGING_SSH_PORT`
- `STAGING_SSH_KEY`
- `STAGING_KNOWN_HOSTS`

O deploy de produção ainda não está automatizado de propósito.

## Próximas fases

1. Implementar o contrato HTTP `POST /v1/audio/transcriptions`.
2. Adicionar decoders/resampling para formatos externos.
3. Selecionar e integrar a primeira engine STT.
4. Fazer benchmark de português brasileiro.
5. Publicar staging isolado na VPS.
