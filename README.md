# Astera Live Transcriber

Serviço independente de transcrição estruturada para o Astera.

## Responsabilidade

```text
ÁUDIO → ASTERA LIVE TRANSCRIBER → TRANSCRIÇÃO ESTRUTURADA
```

Este serviço não interpreta medicina, não gera SOAP, hipóteses ou componentes A2UI. Ele entrega texto, segmentos, revisões, status e timestamps para que o Astera decida o significado clínico.

## Status

`v0.1.0` com integração opcional do engine NVIDIA Parakeet TDT 0.6B v3 INT8 via
`sherpa-onnx`. O default continua sendo `noop`, para que ambientes existentes não
sejam alterados por dependência de modelo ou download automático.

A aplicação possui:

- `GET /health`
- `GET /v1/models`
- modelo de domínio `TranscriptSegment`
- contrato `TranscriptionEnginePort`
- Noop Engine para desenvolvimento
- `POST /v1/audio/transcriptions` para WAV PCM16 mono 16 kHz
- `POST /v1/realtime/files` para iniciar uma sessão live a partir de MP3/WAV
- `WS /v1/realtime/transcription/{session_id}` para consumir os eventos do arquivo
- pipeline realtime em memória com Ring Buffer, VAD RMS e turn detection temporal
- lifecycle `partial` → `revised` → `committed` com identidade estável de segmento
- cadence de partial configurável (`ASTERA_TRANSCRIBER_PARTIAL_INTERVAL_MS`)
- status do engine em `GET /v1/engine/status`
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
curl http://localhost:8000/v1/engine/status
```

### Parakeet local

O modelo não é versionado no Git e não é baixado durante o startup. Para habilitar
a engine localmente:

```bash
python scripts/download_models.py --target-dir models
export ASTERA_TRANSCRIBER_ENGINE=parakeet
export ASTERA_TRANSCRIBER_MODEL_PATH=models/parakeet-tdt-0.6b-v3-int8
python -m pip install -e ".[dev,parakeet]"
uvicorn astera_live_transcriber.main:app --reload
```

O loader verifica `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` e
`tokens.txt`, carrega uma vez e mantém o recognizer residente. Se o diretório
estiver incompleto, o processo falha explicitamente; não há fallback implícito para
`noop`.

Transcrição HTTP:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F file=@sample.wav \
  -F language=pt-BR
```

Benchmark local:

```bash
python scripts/benchmark_stt.py --model-path models/parakeet-tdt-0.6b-v3-int8 sample.wav
```

### Realtime

Conecte em `WS /v1/realtime/transcription`, envie `session.create` e depois eventos `audio.append` com áudio PCM16 mono em base64. O formato canônico desta fase é 16 kHz. O servidor emite `session.created`, `speech.started`, `speech.stop_candidate`, `transcript.partial`, `transcript.revised` e `transcript.committed`.

### Arquivos como live source

Arquivos não usam o caminho batch como experiência principal. O upload cria uma
sessão e retorna imediatamente um `session_id`; o cliente então conecta no WebSocket
da sessão. O processamento começa somente depois desse attach para evitar perder os
eventos iniciais.

```bash
curl -X POST http://localhost:8000/v1/realtime/files \
  -F file=@consulta.mp3 \
  -F model=astera-stt-realtime-1 \
  -F language=pt-BR \
  -F mode=realtime
```

O retorno contém `status=streaming` e o caminho WebSocket. `mode=realtime` respeita
a duração do áudio; `mode=accelerated` entrega os mesmos chunks e passa pelo mesmo
VAD, turn detection, engine e lifecycle no ritmo mais rápido que o pipeline aceitar.
Nos dois modos, o EOF faz flush/force-commit do segmento ativo e produz
`audio.completed` seguido de `session.completed`. O arquivo original é mantido
temporariamente em disco durante a sessão; nenhum PCM completo é materializado.

O endpoint `POST /v1/audio/transcriptions` permanece apenas como API de
compatibilidade secundária.

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

## Limitações conhecidas

- A fronteira HTTP desta fase aceita WAV PCM16 mono em 16 kHz; decoders e resampling
  de outros formatos permanecem uma evolução separada.
- O workflow `Parakeet model test` é manual porque baixa o modelo e pode consumir
  recursos de CI. O workflow `CI` normal continua sem o peso do modelo.
- A fonte de arquivo depende do binário `ffmpeg`, instalado no Docker; fixtures locais
  podem ser puladas em ambientes sem esse executável.
- O deploy de produção ainda não está automatizado; staging permanece isolado.
