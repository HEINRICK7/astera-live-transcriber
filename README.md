# Astera Live Transcriber

> Transcrição de áudio em tempo real para o ecossistema Astera — com baixa latência, revisões ao vivo e arquitetura preparada para evolução clínica segura.

O **Astera Live Transcriber** transforma voz em texto enquanto a conversa acontece. Ele recebe áudio de microfone ou arquivo, envia os dados para uma engine de Speech-to-Text e entrega uma transcrição viva, composta por hipóteses parciais, revisões e trechos confirmados.

O projeto foi desenhado para ser a fundação de uma experiência de documentação clínica mais rápida, sem obrigar o profissional a interromper a consulta para digitar, corrigir ou treinar o sistema.

> **Importante:** o Transcriber é uma camada de captura e transcrição. Ele não faz diagnóstico, não interpreta medicina, não gera SOAP e não substitui a decisão de um profissional de saúde.

## Índice

- [Visão do produto](#visão-do-produto)
- [Estado atual](#estado-atual)
- [Demonstração rápida](#demonstração-rápida)
- [Como a transcrição live funciona](#como-a-transcrição-live-funciona)
- [Arquitetura](#arquitetura)
- [Provider xAI Grok](#provider-xai-grok)
- [Interface e APIs](#interface-e-apis)
- [Inteligência adaptativa](#inteligência-adaptativa)
- [Testes e qualidade](#testes-e-qualidade)
- [Segurança e privacidade](#segurança-e-privacidade)
- [Roadmap](#roadmap)

## Visão do produto

### Problema

Durante uma consulta, registrar tudo manualmente consome atenção, interrompe o fluxo da conversa e aumenta o trabalho administrativo do profissional.

### Solução

O Astera acompanha o áudio em tempo real e apresenta a transcrição progressivamente:

```text
voz
  ↓
captura de áudio
  ↓
Speech-to-Text
  ↓
partial → revised → committed
  ↓
transcrição live para o Astera
```

### Valor entregue

- reduz a necessidade de digitação durante a conversa;
- permite acompanhar a transcrição enquanto o áudio acontece;
- preserva revisões e timestamps para auditoria e integração;
- separa claramente captura/transcrição de interpretação clínica;
- permite trocar a engine de voz sem reescrever o domínio do produto;
- cria uma base para adaptação progressiva sem exigir treinamento manual do usuário.

## Estado atual

O projeto está em fase de MVP técnico demonstrável, com:

- interface web local para microfone e arquivos MP3/WAV;
- streaming realtime por WebSocket;
- integração opcional com xAI Grok Speech-to-Text;
- engine local opcional NVIDIA Parakeet TDT 0.6B v3 INT8;
- pipeline com VAD, Ring Buffer, detecção de turnos e lifecycle de segmentos;
- eventos `partial`, `revised` e `committed` com identidade estável;
- reconexão limitada, backpressure e isolamento entre sessões;
- camada de inteligência adaptativa assíncrona;
- proteção contra duplicações entre segmentos e fechamento explícito de sessão;
- testes unitários, de integração e de contrato;
- execução local, Docker, CI e staging separado.

O MVP ainda não deve ser apresentado como produto clínico validado. A próxima etapa é ampliar benchmarks, soak tests, validação de qualidade e controles de segurança antes de qualquer uso em produção assistencial.

## Demonstração rápida

### 1. Instalar e configurar

Requisitos:

- Python 3.12 ou superior;
- `ffmpeg` para processar arquivos MP3/WAV;
- uma chave xAI válida para usar o provider Grok;
- Docker opcional.

```bash
cd astera-live-transcriber

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

cp .env.example .env
```

Edite o `.env` e configure:

```env
ASTERA_TRANSCRIBER_STT_PROVIDER=xai
ASTERA_TRANSCRIBER_XAI_API_KEY=sua-chave-do-grok
ASTERA_TRANSCRIBER_XAI_ENDPOINT=wss://api.x.ai/v1/stt
```

Nunca coloque a chave no código, no frontend ou em um commit Git. O backend é o único componente que acessa o provider cloud.

### 2. Iniciar o servidor

```bash
source .venv/bin/activate
uvicorn astera_live_transcriber.main:app --reload --port 8000
```

Verifique a saúde da aplicação:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/engine/status
```

### 3. Abrir a demonstração

Abra:

```text
http://127.0.0.1:8000/
```

Na interface:

1. selecione um arquivo MP3 ou WAV;
2. clique em **Tocar e transcrever**;
3. acompanhe o áudio e a transcrição simultaneamente;
4. observe a evolução dos eventos parciais, revisados e confirmados.

Também é possível ligar o microfone diretamente pelo botão **Ligar microfone**.

> Se a porta `8000` estiver ocupada, use `--port 8001` e abra `http://127.0.0.1:8001/`.

### 4. Testar um arquivo pelo terminal

```bash
python scripts/transcribe_file.py ./caminho/consulta.mp3 \
  --server http://127.0.0.1:8000 \
  --mode realtime
```

O modo `realtime` acompanha a duração natural do áudio. O modo `accelerated` usa o mesmo pipeline, mas tenta processar o arquivo mais rapidamente:

```bash
python scripts/transcribe_file.py ./caminho/consulta.mp3 \
  --server http://127.0.0.1:8000 \
  --mode accelerated
```

## Como a transcrição live funciona

Cada sessão mantém seu próprio estado. O áudio passa por um pipeline controlado:

```text
AudioChunk
   ↓
AudioNormalizer
   ↓
RingBuffer + VAD
   ↓
Turn Detection
   ↓
SpeechEnginePort
   ↓
TranscriptEvent
   ├── FAST PATH → UI imediatamente
   └── INTELLIGENCE PATH → observação assíncrona
```

O ciclo de um segmento é:

| Evento | Significado |
|---|---|
| `transcript.partial` | primeira hipótese publicável do segmento |
| `transcript.revised` | hipótese atualizada mantendo o mesmo `segment_id` |
| `transcript.committed` | trecho confirmado no limite de silêncio, turn ou EOF |

O texto live pode mudar enquanto o provider recebe mais áudio, mas o segmento mantém sua identidade. Depois de confirmado, ele não é revisado silenciosamente.

### Projeção estrutural do transcript

O `ProviderSegmentStore` preserva os `committed_text` originais recebidos do
provider para auditoria. A camada `CommittedTranscriptProjector` produz a
visão usada pela interface e por exportações, sem alterar o histórico bruto.

Ela remove somente evidências estruturais comprovadas:

- replay entre segmentos temporal/textualmente sobrepostos;
- snapshots autoritativos que contêm integralmente o segmento anterior;
- duplicações adjacentes dentro de snapshots sobrepostos, como `Como Como` e
  `Perfeito. Perfeito.`.

O evento committed mantém `text` original e expõe `projected_text` como visão
estrutural. As operações aplicadas aparecem em
`technical.committed_projection.projected_transcript` quando o trace está
ativo. Nenhuma correção semântica, fuzzy matching ou alteração de dose ocorre
nessa camada.

### Projeção incremental (experimental)

O runtime mantém a projeção incremental desligada por padrão. Ela preserva a
`evidence_history` imutável e mede um working set local, mas só pode ser
promovida depois de produzir saída equivalente ao projector legado em todos os
fixtures:

```env
ASTERA_TRANSCRIBER_INCREMENTAL_PROJECTION_ENABLED=false
ASTERA_TRANSCRIBER_INCREMENTAL_PROJECTION_WORKING_SET_SIZE=4
```

Compare os caminhos sem alterar a sessão realtime:

```bash
python scripts/compare_projection_modes.py \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_002_full_capture/events.json \
  --working-set-size 4 \
  --output /tmp/incremental-projection-comparison.json

python scripts/analyze_projection_divergence.py \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_002_full_capture/events.json \
  --working-set-size 4 \
  --output /tmp/projection-divergence.json
```

O segundo relatório localiza o primeiro mismatch, seus contextos, operações,
working set e candidatos de causa. A flag só deve ser ativada com
`mismatch_count=0`; no run de 25 minutos versionado, a política indexada
atinge 87/87 commits equivalentes, com no máximo 8 segmentos no working set e
241 operações contra 2.590 do projector legado. A flag continua experimental
até um novo run realtime confirmar a mesma equivalência com a captura completa.

## Arquitetura

O projeto segue uma separação de responsabilidades simples:

```text
presentation → application → domain
infrastructure → application/domain
```

### Domain

Contém entidades, eventos e regras estruturais: sessão, segmento, timestamps, status e modelos da inteligência adaptativa.

### Application

Contém portas e casos de uso independentes de FastAPI, Docker, provider cloud ou banco de dados. O contrato principal é `TranscriptionEnginePort`; engines são adaptadores substituíveis.

### Infrastructure

Contém VAD, Ring Buffer, configuração, runtime de engines, integração xAI, Parakeet, memória em processo, RapidFuzz e avaliação JiWER.

### Presentation

Contém a API HTTP, WebSockets e a interface web local.

O ponto de composição é [`presentation/api/dependencies.py`](src/astera_live_transcriber/presentation/api/dependencies.py). A engine atual pode ser trocada sem alterar o domínio ou o contrato de eventos.

## Provider xAI Grok

O provider xAI é opcional e passa pelo mesmo pipeline interno do Astera. A integração usa WebSocket, áudio PCM16 binário, resultados intermediários, finalização explícita e reconexão limitada.

Configuração mínima:

```env
ASTERA_TRANSCRIBER_STT_PROVIDER=xai
ASTERA_TRANSCRIBER_XAI_API_KEY=sua-chave-do-grok
ASTERA_TRANSCRIBER_XAI_ENDPOINT=wss://api.x.ai/v1/stt
```

Características importantes:

- a API key fica somente no backend;
- cada conexão cria sua própria engine, fila e estado;
- sessões não compartilham hipóteses ou buffers;
- o provider é convertido para eventos neutros do Astera;
- timestamps e palavras são preservados quando fornecidos;
- erros de autenticação, rate limit e indisponibilidade são classificados;
- a fila de áudio é bounded e usa backpressure: em arquivo realtime o produtor
  aguarda o dreno da fila, sem descartar PCM;
- stall prolongado aciona reconexão controlada; chunks já retirados para envio
  não são reenviados sem confirmação do provider, evitando duplicação ambígua;
- a telemetria registra `audio_queue_size`, `queue_utilization_pct`,
  `producer_wait_ms`, `provider_send_latency_ms`, `stall_duration_ms`,
  `reconnect_count`, `inflight_audio_ms`, `backlog_audio_ms` e
  `dropped_audio_ms`;
- o benchmark deve medir latência, estabilidade, qualidade e custo.

Parâmetros de backpressure:

```env
ASTERA_TRANSCRIBER_STT_AUDIO_QUEUE_SIZE=32
ASTERA_TRANSCRIBER_STT_QUEUE_HIGH_WATER_MARK=0.8
ASTERA_TRANSCRIBER_STT_PROVIDER_STALL_TIMEOUT_MS=5000
```

Em microfone, a captura não deve ser tratada como arquivo: o cliente precisa
manter seu próprio buffer/spool quando a rede estiver indisponível. O adapter
mede o stall e o áudio ambíguo em trânsito; ele não mascara falhas como uma
sessão concluída com sucesso.

Para voltar ao modo local:

```env
ASTERA_TRANSCRIBER_STT_PROVIDER=local
ASTERA_TRANSCRIBER_ENGINE=noop
```

## Interface e APIs

### Interface web

A interface em [`presentation/web/index.html`](src/astera_live_transcriber/presentation/web/index.html) permite:

- testar arquivos MP3/WAV;
- iniciar áudio e transcrição com uma única ação;
- acompanhar progresso e eventos técnicos;
- usar o microfone em streaming;
- visualizar texto parcial, revisado e confirmado.

### Endpoints principais

| Método | Endpoint | Uso |
|---|---|---|
| `GET` | `/health` | saúde da aplicação |
| `GET` | `/v1/models` | modelos públicos disponíveis |
| `GET` | `/v1/engine/status` | provider, modelo e estado da engine |
| `POST` | `/v1/audio/transcriptions` | transcrição HTTP compatível |
| `POST` | `/v1/realtime/files` | cria uma sessão realtime a partir de arquivo |
| `WS` | `/v1/realtime/transcription/{session_id}` | acompanha uma sessão de arquivo |
| `WS` | `/v1/realtime/transcription` | streaming direto de microfone/cliente |

### Streaming direto

Para uma conexão WebSocket direta, envie primeiro:

```json
{
  "type": "session.create",
  "session": {
    "model": "astera-stt-realtime-1",
    "language": "pt-BR"
  }
}
```

Depois envie eventos `audio.append` contendo áudio PCM16 mono em base64, preferencialmente a 16 kHz. Ao finalizar, envie `session.close`.

## Inteligência adaptativa

O Transcriber observa a evolução da transcrição sem exigir qualquer treinamento manual do profissional:

```text
TranscriptEvent
      ↓
Observer assíncrono bounded
      ├── RevisionLearner
      ├── RepetitionLearner
      ├── Session Vocabulary
      ├── RapidFuzz candidate retrieval
      ├── Candidate Ranker
      └── Keyterm Selector
```

Princípios de segurança:

- o caminho de inteligência nunca bloqueia a entrega live;
- a fila é limitada e descarta eventos de baixa prioridade sob pressão;
- o texto original permanece imutável;
- qualquer normalização possui decisão, confiança e provenance;
- similaridade fuzzy sozinha nunca corrige texto;
- doses, números, datas, negação, lateralidade, alergias, diagnósticos e medicamentos são tratados como alto risco;
- contradições mantêm candidatos concorrentes e bloqueiam automação insegura;
- memória e padrões são isoláveis por provider;
- não há fine-tuning ou treinamento neural durante a conversa.

### Retenção

| Política | Comportamento |
|---|---|
| `none` | não armazena sinais adaptativos |
| `session_only` | mantém sinais durante a sessão e limpa no encerramento |
| `derived_patterns_only` | promove apenas padrões derivados recorrentes |
| `explicit_dataset` | reservado para fluxo futuro de dataset explicitamente aprovado |

A normalização automática fica desligada por padrão. Para habilitá-la somente para candidatos LOW RISK com evidência recorrente:

```env
ASTERA_TRANSCRIBER_INTELLIGENCE_NORMALIZATION_ENABLED=true
```

JiWER pertence exclusivamente ao benchmark offline; não está no caminho realtime.

Com `ASTERA_TRANSCRIBER_STT_DEBUG_TRACE=true`, a telemetria do observer também
emite linhas `[adaptive.match]` com os scores RapidFuzz (`ratio`,
`partial_ratio`, `token_sort_ratio`, `token_set_ratio`, `wratio`) e a decisão
`ignored`, `candidate` ou `promoted`. Essa telemetria é assíncrona e não altera
o texto entregue.

Para benchmark offline, use `JiwerBenchmark` com uma referência revisada e
hipóteses nomeadas, por exemplo `xai_raw_committed`,
`astera_structural_projection` e `astera_structural_adaptive`. O relatório
calcula WER, CER, MER e WIL por variante.

O primeiro fixture oficial está documentado em
[`benchmarks/pt_br/clinical/consultation_001/README.md`](benchmarks/pt_br/clinical/consultation_001/README.md).
O comando `scripts/run_jiwer_benchmark.py` gera uma tabela no terminal e um
relatório JSON. A referência precisa ser manual e fiel ao áudio; saída do xAI
ou do Astera nunca deve ser usada como ground truth.

Para capturar uma execução realtime do áudio e gerar as três hipóteses de
laboratório:

```bash
python scripts/capture_realtime_benchmark.py \
  /caminho/audio_2min.mp3 \
  --output-dir benchmarks/pt_br/clinical/consultation_001
```

O script salva `events.json`, `xai_raw_committed.txt` e
`astera_structural_projection.txt`. Como a normalização adaptativa está
desligada por padrão, `astera_structural_adaptive.txt` é criado explicitamente
igual ao structural baseline e não deve ser interpretado como ganho.

## Engine local Parakeet

O modelo não é versionado no Git nem baixado durante o startup. Para usar Parakeet localmente:

```bash
python scripts/download_models.py --target-dir models
python -m pip install -e ".[dev,parakeet]"

export ASTERA_TRANSCRIBER_STT_PROVIDER=local
export ASTERA_TRANSCRIBER_ENGINE=parakeet
export ASTERA_TRANSCRIBER_MODEL_PATH=models/parakeet-tdt-0.6b-v3-int8

uvicorn astera_live_transcriber.main:app --reload
```

O loader verifica `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` e `tokens.txt`. Se o diretório estiver incompleto, a aplicação falha explicitamente; não existe fallback silencioso para `noop`.

Benchmark local:

```bash
python scripts/benchmark_stt.py \
  --model-path models/parakeet-tdt-0.6b-v3-int8 \
  ./caminho/consulta.wav
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Por padrão, a aplicação ficará disponível em `http://127.0.0.1:8000/`. A porta externa pode ser alterada com:

```env
ASTERA_TRANSCRIBER_HOST_PORT=8001
```

O volume `./models:/models:ro` permite usar o modelo Parakeet local sem incluí-lo na imagem.

## Testes e qualidade

Executar a suíte completa:

```bash
source .venv/bin/activate
ruff check src tests
pytest -q
python -m pip check
python -m compileall -q src tests
```

A cobertura inclui:

- lifecycle de sessões e segmentos;
- VAD, turn detection, Ring Buffer e backpressure;
- partial → revised → committed;
- deduplicação de replay entre segmentos;
- fechamento limpo de WebSocket;
- integração xAI sem chamadas de rede nos testes normais;
- segurança da camada adaptativa;
- retenção, decay, contradição e isolamento por provider;
- JiWER com referência vazia para medir inserções em silêncio.

Para benchmarks de áudio, consulte [`docs/audio-benchmark.md`](docs/audio-benchmark.md). O benchmark deve comparar sempre a mesma gravação, medindo:

- tempo até o primeiro partial (TTFP);
- latência p50/p95;

### Atribuição de qualidade textual

Depois de uma captura longa, a análise offline compara o committed bruto com o
`projected_text` sem modificar nenhum evento:

```bash
.venv/bin/python scripts/analyze_quality_attribution.py \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/events.json \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/quality-attribution.json
```

O relatório distingue `provider_adjacent_duplication`, `provider_replay`,
`provider_language_drift` e `projector_adjacent_duplication`, preservando o
texto original. Erros de medicamento, dose, número e negação não são inferidos
por este analisador: exigem `reference.txt` e avaliação clínica independente.

O Safe Structural Cleanup permanece em shadow:

```bash
.venv/bin/python scripts/evaluate_safe_cleanup_shadow.py \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/events.json \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/safe-cleanup-shadow.json
```

`projected_text` e `committed_text` continuam sendo preservados. O campo
`projected_text_clean` é experimental e não substitui a transcrição exibida
até que os gates de regressão e fidelidade clínica sejam aprovados. O relatório
separa `snapshot_operations_total` de `unique_cleanup_spans`, registra
`first_seen_revision`/`last_seen_revision` e gera o diff do último transcript.

Para explicar pendências sem criar heurísticas novas:

```bash
.venv/bin/python scripts/analyze_unresolved_cleanup.py \
  --quality-attribution benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/quality-attribution.json \
  --cleanup-shadow benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/safe-cleanup-shadow.json \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/events.json \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/unresolved-cleanup-analysis.json
```

Para investigar somente as fronteiras entre segmentos:

```bash
.venv/bin/python scripts/analyze_cross_segment_cleanup.py \
  --unresolved benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/unresolved-cleanup-analysis.json \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/events.json \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/cross-segment-cleanup-analysis.json
```

Essa etapa apenas mede overlap exato de cauda/cabeça e proximidade temporal;
não remove spans e não habilita cleanup.

### Clinical Long-Session Quality Benchmark

O Safe Cleanup fica congelado em shadow: não há limpeza cross-segment ou de
pontuação promovida automaticamente. O próximo estágio é medir a qualidade
clínica do áudio longo por amostragem, sem criar ground truth a partir do xAI.

O preparador abaixo cria clipes de 45 segundos distribuídos pelos cinco
intervalos da sessão e amostras direcionadas para anamnese, negações, números,
exame físico, medicamentos e termos clínicos difíceis:

```bash
PYTHONPATH=src .venv/bin/python scripts/prepare_clinical_long_benchmark.py \
  --events benchmarks/pt_br/clinical/consultation_002_soak_25min/runs/run_006_incremental_backpressure_metrics/events.json \
  --audio /home/carlos-henrique/Músicas/medical.mp3 \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006
```

Para cada `sample_*/audio.wav`, o revisor deve ouvir o áudio e criar
`reference.txt` literalmente, mantendo hesitações, repetições legítimas,
negações, números, unidades e speakers. O arquivo
`reference.pending.txt` é apenas uma instrução e nunca é usado como referência.

O pacote também cria `clinical_annotations.json` com status pendente. Depois da
revisão, altere o status para `validated` e preencha apenas o que puder ser
confirmado pelo áudio. O gate pode ser conferido sem executar JiWER:

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_clinical_sample_annotations.py \
  --samples benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006 \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006/annotation-gate.json
```

Enquanto o run de origem não tiver `projected_text_clean` local por segmento,
o validador mantém o benchmark bloqueado. Isso evita comparar uma referência
de 45 segundos contra um snapshot estrutural acumulado de 25 minutos.

Para validar primeiro somente o piloto de três amostras:

```bash
PYTHONPATH=src .venv/bin/python scripts/create_clinical_pilot.py \
  --manifest benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006/sampling-manifest.json \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006/pilot

PYTHONPATH=src .venv/bin/python scripts/validate_clinical_sample_annotations.py \
  --samples benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006 \
  --sample-id sample_002 --sample-id sample_015 --sample-id sample_019 \
  --output benchmarks/pt_br/clinical/consultation_002_soak_25min/clinical-quality-samples/run_006/pilot/annotation-gate.json
```

O `sampling-manifest.json` informa termos encontrados e ausentes na evidência
capturada. A ausência de uma palavra no transcript do provider é um resultado
do diagnóstico, não autorização para inventá-la na referência.

Importante: o run 006 preserva `projected_text` como snapshot acumulado. Por
isso o preparador salva esses snapshots para auditoria, mas marca a hipótese
estrutural como `cumulative_snapshot_not_segment_isolated`; nenhum WER é
calculado até a hipótese local e o `reference.txt` serem revisados
independentemente.

Depois da revisão manual, cada amostra pode ser avaliada com
`scripts/run_jiwer_benchmark.py`, usando as três variantes e um arquivo de
anotações clínicas validado. O relatório deve guardar WER, CER, MER, WIL,
fidelidade de speaker/medicamento/dose/unidade/negação e CCER experimental.
- lag acumulado;
- revisões e commits;
- erros e reconexões;
- CPU e memória;
- qualidade e custo por duração de áudio.

Gates desejados para evolução do produto:

```text
TTFP < 1,5 s
lag p95 < 1,5 s
zero acúmulo progressivo de lag
zero correção automática de alto risco
zero regressão perceptível causada pelo learner
```

## Staging

O workflow `Deploy staging` é manual (`workflow_dispatch`) e usa um projeto Docker separado em `/opt/astera-live-transcriber-staging`, na porta `48080`. Ele não reinicia nem altera projetos existentes.

Antes de executar o workflow, configure no ambiente `staging` do GitHub:

- `STAGING_HOST`;
- `STAGING_USER`;
- `STAGING_SSH_PORT`;
- `STAGING_SSH_KEY`;
- `STAGING_KNOWN_HOSTS`.

O deploy de produção ainda não é automatizado.

## Segurança e privacidade

- não versionar `.env`, API keys, áudios clínicos ou modelos;
- não enviar a chave xAI para o navegador;
- não reter áudio de produção automaticamente;
- usar somente dados sintéticos, públicos/licenciados, consentidos ou explicitamente aprovados em benchmarks;
- tratar qualquer normalização como reversível e auditável;
- manter a separação entre transcrição e interpretação clínica;
- validar requisitos legais, de privacidade e de segurança antes de uso assistencial.

## Limitações conhecidas

- a API HTTP compatível aceita WAV PCM16 mono em 16 kHz;
- arquivos MP3/WAV dependem do `ffmpeg` instalado;
- o provider xAI exige credencial e conectividade externa;
- a qualidade pode variar por ruído, microfone, sotaque, sobreposição de falas e domínio;
- o MVP ainda não possui validação clínica, armazenamento persistente de produção ou painel de métricas operacional;
- o modelo Parakeet local exige download separado e recursos compatíveis;
- a validação de longa duração deve ser executada antes de declarar estabilidade de produção.

## Roadmap

### Próxima etapa

- concluir soak tests de 10 e 30 minutos;
- medir TTFP, lag p95, CPU, RAM e custo com áudio representativo;
- ampliar a validação do xAI em português brasileiro;
- criar dataset de avaliação separado em tuning, validation e test;
- evoluir observabilidade e exportação de métricas.

### Evolução do produto

- integração com o fluxo de documentação do Astera;
- vocabulários de domínio com governança e provenance;
- autenticação e autorização por ambiente;
- armazenamento persistente somente quando aprovado pela política de privacidade;
- benchmark contínuo por provider e versão;
- rollout controlado de qualquer mudança de modelo ou normalização.

## Estrutura do projeto

```text
src/astera_live_transcriber/
├── domain/              # entidades, eventos e regras do domínio
├── application/         # portas, serviços e casos de uso
├── infrastructure/      # engines, áudio, config, providers e métricas
└── presentation/        # API, WebSockets e interface web

tests/
├── unit/                # comportamento isolado
├── integration/         # API, WebSockets e sessões de arquivo
└── contract/            # contratos externos
```

## Licença e uso

Projeto proprietário do ecossistema Astera. O uso, distribuição e integração devem seguir as políticas definidas pelos responsáveis pelo produto e pelos dados processados.

---

**Astera Live Transcriber** — o profissional fala, o Astera acompanha, e a tecnologia trabalha para devolver tempo à consulta.
