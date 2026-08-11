# Visão arquitetural

O serviço usa quatro círculos simples:

1. **Domain** — entidades, eventos e regras estruturais de transcrição.
2. **Application** — portas, serviços e casos de uso independentes de transporte.
3. **Infrastructure** — configuração, autenticação, áudio e adaptadores de engine.
4. **Presentation** — FastAPI e WebSocket como detalhes de entrega.

O ponto de composição é `presentation/api/dependencies.py`. A engine real poderá substituir `NoopTranscriptionEngine` sem alterar o contrato HTTP ou o domínio.

## Pipeline realtime

Cada conexão cria uma `TranscriptionSession`, um `RingBuffer`, um VAD, um `TurnDetectionPort`, uma engine e um `SegmentLifecycleService`. O pipeline só publica texto que passa por `is_publishable_text`; silêncio puro, texto vazio e pontuação isolada não viram evidência. Revisões incrementam `revision` e preservam o mesmo `segment_id`; após `committed`, o segmento não é alterado silenciosamente.
