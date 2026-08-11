# Áudio de benchmark local

Áudios reais de desenvolvimento não fazem parte do repositório. Para o arquivo
clínico de aproximadamente 25 minutos, mantenha uma cópia local em
`benchmarks/audio/` e registre somente os metadados abaixo:

```text
duration: ~25 minutes
language: pt-BR
speakers: 2+
environment: room
domain: clinical simulation
format: MP3
```

Use esse arquivo apenas no benchmark manual, medindo CPU, RAM, RTF, tempo até o
primeiro partial, revisões, commits e estabilidade de longa duração. Fixtures
pequenas para CI devem ficar em `tests/fixtures/audio/`; os formatos binários são
ignorados pelo Git.

```bash
python scripts/benchmark_stt.py \
  --model-path models/parakeet-tdt-0.6b-v3-int8 \
  benchmarks/audio/consulta.mp3
```
