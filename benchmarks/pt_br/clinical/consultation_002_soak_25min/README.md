# Consultation 002 — soak realtime de aproximadamente 25 minutos

Este fixture preserva os dados fornecidos após a execução de um áudio clínico
longo. Ele serve para avaliar estabilidade operacional e orientar a próxima
análise de qualidade.

## Arquivos

- `technical-trace-excerpt.txt`: trecho técnico RAW → MAPPED → CANONICAL;
- `projected-transcript.txt`: transcrição projetada anexada pelo usuário;
- `metadata.json`: métricas extraídas exclusivamente dos anexos.
- `events.json`: eventos estruturados do trecho disponível;
- `long-session-analysis.json`: análise offline desse trecho/run;
- no run completo, a seção `degradation` contém diagnóstico por commit,
  proxies de histórico e correlações de custo estrutural.

O primeiro recorte estruturado foi gerado com:

```bash
python scripts/parse_soak_trace_excerpt.py \
  --input technical-trace-excerpt.txt \
  --output events.json
```

## Resultado operacional observado

- duração observada: `1.495.240 ms` (`24m55s`);
- último segmento visível: `seg_0098`;
- último sequence visível: `1659`;
- `audio.completed`: presente;
- `session.completed`: presente;
- WebSocket `1000`, `clean=true`;
- revisões temporais com `replace_overlap` continuaram funcionando.

## Limitação importante

Este não é o `events.json` completo da execução. O anexo começa no trecho
`seq=1583`, portanto as contagens em `metadata.json` são contagens do trecho
fornecido, não necessariamente do run inteiro. Não foram inventadas métricas
de memória, fila, pausas, drift ou segmentos de duração zero.

O transcript ainda contém artefatos como `Boa tarde. Então, meu Boa tarde`,
`Está Está`, `Pode Pode`, `Se Se` e `Então, Então`. Por isso este run fica
classificado como evidência de estabilidade operacional, não como benchmark de
qualidade clínica.

Quando o `events.json` completo estiver disponível, gerar a análise com:

```bash
python scripts/analyze_long_session.py \
  --events events.json \
  --source-label consultation_002_soak_25min \
  --output long-session-analysis.json
```

## Captura persistente do novo run

O capturador versiona cada execução e grava o evento imediatamente em
`events.jsonl` usando `flush + fsync`. O `events.json` e os demais artefatos são
gerados somente ao final, sem apagar o `events.jsonl` se o processo cair:

```bash
python scripts/capture_realtime_benchmark.py \
  /caminho/para/clinical.mp3 \
  --server http://127.0.0.1:8001 \
  --output-dir runs/run_002_full_capture
```

Arquivos gerados no run:

- `events.jsonl`: evidência primária, append por evento;
- `events.json`: agregado final;
- `technical-summary.json`: contagens, sequências, sessão e fechamento;
- `projected-transcript.txt`: projeção estrutural;
- `metadata.json`: origem e política de persistência.

Depois do fechamento da sessão:

```bash
PYTHONPATH=src python scripts/analyze_long_session.py \
  --events runs/run_002_full_capture/events.json \
  --source-label consultation_002_soak_25min_run_002 \
  --output runs/run_002_full_capture/long-session-analysis.json
```

## Projeção incremental — experimento controlado

O modo incremental foi implementado atrás de uma flag e permanece desligado
por padrão até passar no gate de equivalência do run longo:

```bash
ASTERA_TRANSCRIBER_INCREMENTAL_PROJECTION_ENABLED=true
ASTERA_TRANSCRIBER_INCREMENTAL_PROJECTION_WORKING_SET_SIZE=4
```

Para comparar sem alterar o runtime:

```bash
PYTHONPATH=src python scripts/compare_projection_modes.py \
  --events runs/run_002_full_capture/events.json \
  --working-set-size 4 \
  --output runs/run_002_full_capture/incremental-projection-comparison.json
```

O modo só deve ser promovido quando `mismatch_count=0` nos fixtures aprovados.

## Clinical Long-Session Quality Benchmark

O run 006 passou nos gates de transporte e projeção, mas ainda não possui
ground truth clínico manual. Para preparar uma avaliação auditável por
amostragem:

```bash
PYTHONPATH=src .venv/bin/python scripts/prepare_clinical_long_benchmark.py \
  --events runs/run_006_incremental_backpressure_metrics/events.json \
  --audio /home/carlos-henrique/Músicas/medical.mp3 \
  --output clinical-quality-samples/run_006
```

O comando cria amostras de 45 segundos nos buckets `0–5`, `5–10`, `10–15`,
`15–20` e `20–25` minutos, além de recortes clínicos direcionados. Cada pasta
contém `audio.wav`, evidência raw, snapshots estruturais, `sample.json` e
`reference.pending.txt`. O revisor deve criar `reference.txt` somente após
ouvir o áudio; não copie qualquer saída do xAI/Astera para esse arquivo.

O manifesto também registra quando um termo clínico não apareceu na evidência
capturada. Isso deve ser tratado como achado de qualidade, não corrigido no
ground truth. Como o run 006 possui snapshots estruturais cumulativos, os
snapshots salvos não são considerados hipóteses locais prontas para JiWER sem
validação adicional.
