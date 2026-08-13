# Consultation 001 — fixture de qualidade

Fixture oficial do áudio clínico de laboratório de aproximadamente 2 minutos.

## Estado

Fixture preenchido com a referência fornecida para o áudio e com o primeiro
benchmark oficial executado. A referência preserva os rótulos `Médica` e
`Paciente` e não deve ser substituída por saída do provider.

Não use logs do provider como referência. Eles são hipóteses e podem conter
revisões, replay e duplicações — justamente os defeitos que o benchmark deve
medir.

## Convenção de arquivos

```text
consultation_001/
├── audio.mp3
├── reference.txt
├── xai_raw_committed.txt
├── astera_structural_projection.txt
├── astera_structural_adaptive.txt
├── clinical_annotations.json
├── clinical_annotations.candidate.json
├── jiwer-baseline.json
├── jiwer-report.json
├── adaptive-shadow-report.json
├── error-analysis-report.json
├── long-session-analysis.json
└── metadata.json
```

Quando a referência estiver revisada, execute na raiz do projeto:

```bash
python scripts/run_jiwer_benchmark.py \
  --reference benchmarks/pt_br/clinical/consultation_001/reference.txt \
  --raw benchmarks/pt_br/clinical/consultation_001/xai_raw_committed.txt \
  --structural benchmarks/pt_br/clinical/consultation_001/astera_structural_projection.txt \
  --adaptive benchmarks/pt_br/clinical/consultation_001/astera_structural_adaptive.txt \
  --output benchmarks/pt_br/clinical/consultation_001/jiwer-report.json
```

As três hipóteses devem vir da mesma gravação e execução equivalente. A
variante adaptive não deve ser considerada uma melhoria enquanto a
normalização automática estiver desligada; nesse caso ela pode ser idêntica à
projeção estrutural, servindo como baseline observacional.

Os checks clínicos usam trechos anotados manualmente em
`clinical_annotations.json`. O arquivo deve conter somente evidências
confirmadas no áudio, organizadas em `dose`, `number`, `negation`,
`medication` e `speaker_label`. O template está em
`clinical_annotations.template.json`. Para incluí-los no relatório:

```bash
python scripts/run_jiwer_benchmark.py \
  --reference reference.txt \
  --raw xai_raw_committed.txt \
  --structural astera_structural_projection.txt \
  --adaptive astera_structural_adaptive.txt \
  --clinical-annotations clinical_annotations.json \
  --output jiwer-report.json
```

Esses checks medem cobertura exata dos spans anotados; não substituem revisão
clínica, precisão semântica nem o benchmark JiWER.

`clinical_annotations.candidate.json` preserva o histórico da anotação inicial;
`clinical_annotations.json` é a versão validada usada no relatório atual.

`jiwer-baseline.json` congela o primeiro resultado oficial. O modo Shadow
registra candidatos RapidFuzz, os cinco scorers e `would_replace`, mas mantém
`shadow_text == structural_text` e nunca altera `projected_text`.

O baseline agora preserva duas visões: `raw_jiwer` (com quebras de linha) e
`normalized_jiwer` (quebras de linha colapsadas). Para qualidade linguística,
use a visão normalizada; a visão raw documenta o impacto da representação.

`clinical_fidelity` valida contexto, speaker, valor, unidade e polaridade. O
`ccer_experimental` só é calculado quando esses campos estruturados existem;
os antigos `clinical_checks` continuam no relatório apenas para comparação
histórica.

`error-analysis-report.json` atribui as 40 operações do alinhamento JiWER e
também calcula uma leitura suplementar normalizando quebras de linha. Essa
leitura não substitui o baseline congelado; ela revela quanto do WER bruto é
apenas diferença de representação entre linhas de diálogo.

`long-session-analysis.json` é gerado pelo analisador offline de sessões longas
e contém métricas por segmento, drift temporal, overlaps, replays, duplicações,
pausas e timeline em buckets de cinco minutos.
