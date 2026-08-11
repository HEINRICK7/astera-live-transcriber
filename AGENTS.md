# Guia do projeto

## Regra de dependências

As dependências apontam para dentro:

```text
presentation → application → domain
infrastructure → application/domain
```

O domínio e os casos de uso não importam FastAPI, Docker, engine STT, banco ou cliente externo.

## Engine STT

Engines são adaptadores de `TranscriptionEnginePort`. Os nomes públicos dos modelos são estáveis (`astera-stt-1` e `astera-stt-realtime-1`); nomes de modelos internos não devem vazar pela API.

## Escopo atual

O bootstrap deliberadamente não inclui engine real, VAD, Redis, PostgreSQL ou armazenamento de áudio. Cada dependência nova deve responder a uma necessidade concreta do fluxo.

## Streaming

O VAD e o turn detector são portas independentes da engine. A sessão, o Ring Buffer e o lifecycle de segmentos são criados por conexão WebSocket; nenhum estado realtime pode ser global. Uma pausa curta mantém o segmento aberto, enquanto `commit_silence_ms` e `max_segment_duration_ms` controlam commits.

O Noop Engine permanece sem texto por padrão para nunca transformar silêncio em evidência artificial. Testes podem injetar uma implementação determinística para validar revisões e commits sem instalar um modelo STT.
