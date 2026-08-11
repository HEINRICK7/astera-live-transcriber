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

