# Visão arquitetural

O serviço usa quatro círculos simples:

1. **Domain** — entidades, eventos e regras estruturais de transcrição.
2. **Application** — portas, serviços e casos de uso independentes de transporte.
3. **Infrastructure** — configuração, autenticação, áudio e adaptadores de engine.
4. **Presentation** — FastAPI e WebSocket como detalhes de entrega.

O ponto de composição é `presentation/api/dependencies.py`. A engine real poderá substituir `NoopTranscriptionEngine` sem alterar o contrato HTTP ou o domínio.

## Providers cloud

`application/ports/transcription_engine.py` define o contrato neutro para sessões
streaming. O adapter `infrastructure/speech/cloud/xai` traduz a conexão e os eventos
do xAI para eventos do engine; nenhuma rota, UI ou regra de segmento conhece tipos do
provider. `SpeechProviderRegistry` fica na composição e permite registrar futuros
providers sem alterar o pipeline.

Engines cloud stateful são criadas por conexão via `EngineRuntime.create_session_engine`.
Isso evita compartilhar WebSocket, fila, hipóteses parciais ou estado de reconexão entre
sessões realtime.

## Pipeline realtime

Cada conexão cria uma `TranscriptionSession`, um `RingBuffer`, um VAD, um `TurnDetectionPort`, uma engine e um `SegmentLifecycleService`. O pipeline só publica texto que passa por `is_publishable_text`; silêncio puro, texto vazio e pontuação isolada não viram evidência. Revisões incrementam `revision` e preservam o mesmo `segment_id`; após `committed`, o segmento não é alterado silenciosamente.

## Intelligence path

Depois de publicar cada evento canônico, o pipeline faz apenas um `observe` síncrono
e não bloqueante em `TranscriptionObserver`. O worker bounded processa revisions,
repetição, candidatos RapidFuzz, memória por provider, seleção de keyterms e decisões
de normalização. A representação mantém `raw_text` e `normalized_text` separados;
normalizações automáticas exigem evidência recorrente, baixo risco e provenance.

`NONE` não armazena sinais, `SESSION_ONLY` limpa os dados ao fechar a sessão e as
políticas derivadas promovem somente padrões recorrentes. Doses, números, negação,
lateralidade e alterações semânticas médicas são bloqueadas. JiWER fica em
`infrastructure/transcription_intelligence/evaluation` e só é usado em benchmarks
offline.
