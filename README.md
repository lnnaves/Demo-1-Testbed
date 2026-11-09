# Demo-1-Testbed

Testbed para executar protocolos de comunicação entre drones num cenário
emulado: containers Docker como drones, rádio Wi-Fi emulado (Containernet +
Mininet-WiFi + wmediumd), roteamento em malha BATMAN-adv, captura PCAP em
cada participante e relatórios de latência, entrega/perda, rotas e sinal.

O produto é a **plataforma**, não os executáveis de exemplo. Quem testa um
protocolo (ex.: EDHOC, PQ-EDHOC, TESLA) troca `bin/sender` e `bin/receiver`
pelos seus programas, descreve o cenário em `scripts/config.yml` e recebe os
resultados numa pasta por execução. O testbed não implementa, não interpreta
e não altera nenhum protocolo: nunca lê o conteúdo dos pacotes nem a saída
dos programas para decidir algo.

**Estado (09/11/2026):** semanas 1 a 4 do plano concluídas e confirmadas em
execução real (unicast, broadcast, latência e perda de rede, mobilidade,
multi-hop). Explicação completa, com resultados e limitações:
[`docs/TESTBED_GUIA_COMPLETO.md`](docs/TESTBED_GUIA_COMPLETO.md).

## Fluxo de uma execução

```text
sudo python3 scripts/run.py [config.yml]
  ├── valida o config (campo desconhecido = erro)
  ├── cria logs/<experimento>/<data-hora>/ e grava metadados (versões)
  ├── limpa restos de execuções anteriores
  ├── sobe os containers, o rádio emulado e o BATMAN-adv; atribui IPs
  ├── espera a malha: ping do sender a cada receiver; limpa o ARP
  ├── inicia um tcpdump por participante (bat0)
  ├── inicia os receivers, depois o sender
  │     ├── move os nós com trajetória (a cada 0,1 s)
  │     └── registra rotas do BATMAN e sinal do Wi-Fi (a cada 1 s)
  ├── encerra o sender (fim por conta própria ou por duração), espera o drain,
  │   encerra os receivers e as capturas
  ├── casa os pacotes entre os PCAPs: latência e perda por sentido
  ├── grava summary.json e CSVs
  └── desmonta a rede (timing.json com o tempo de cada fase)
```

## Configuração

Há um único config versionado, `scripts/config.yml`; outros podem ser
passados na linha de comando. Cada execução roda **um** modo, escolhido em
`protocol.mode`:

```yaml
experiment:
  id: exp-01-unicast
  end: duration          # sender: termina quando o sender sai sozinho
                         # duration: o testbed encerra o sender no limite
  duration_seconds: 10   # conta a partir do início do sender
  drain_seconds: 1.0     # espera após o sender, antes de parar receivers

protocol:
  mode: unicast          # ou: broadcast
  port: 5000
  sender: drone1
  receivers:
    - gcs                # broadcast aceita vários receivers

nodes:
  - id: drone1
    ip: 192.168.123.2/24
    position: [20, 0, 0] # metros; onde fica durante a espera da malha
    mobility:            # opcional
      type: trajectory
      waypoints:         # [segundo, x, y, z]; ou waypoints_csv: arquivo.csv
        - [0, 20, 0, 0]
        - [30, 150, 0, 0]
```

- Em `unicast`, o sender recebe o IP do único receiver; em `broadcast`,
  `wireless.broadcast_ip`, e todos os receivers são iniciados.
- `end: sender` serve para protocolos que terminam sozinhos (ex.: um
  handshake); `duration_seconds` vira um limite de segurança, e estourá-lo é
  falha (`sender timed out`). `end: duration` serve para tráfego contínuo
  (ex.: telemetria); ser encerrado no limite é o fim normal.
- A trajetória começa quando o sender começa; entre os pontos o nó anda em
  linha reta e, depois do último, fica parado. O primeiro ponto deve ser
  `t = 0` e igual a `position`.
- Campos desconhecidos são erro antes de rodar: com `postion:`, a execução
  para com `configuration error: nodes[0]: unknown field 'postion'`.
- A captura grava todo o tráfego da `bat0`; `capture.filter` aceita um filtro
  do tcpdump opcional.

## Alcance do rádio e posições

Não existe um alcance fixo por nó. O emulador calcula o sinal entre cada par
de nós a partir de `txpower`, do modelo de propagação e da distância, com uma
variação aleatória (*fading*); abaixo de `wireless.noise_threshold_dbm` o
quadro não chega.

O testbed **não garante conectividade**: ele mostra o que acontece. Com os
valores padrão e um drone se afastando, a entrega foi de 100% até ~84 m, a
latência começou a subir antes (~70 m) e a perda cresceu até quase 100% perto
de 150 m, sem corte abrupto. Com um relay no meio do caminho, o BATMAN passou
a rotear por ele. Se nenhum caminho existir no início, a execução falha na
espera da malha com `network not ready`.

## Integração do protocolo

Contrato atual (será trocado por um comando com marcadores na semana 6),
detalhado em [`bin/BIN_INTERFACE.md`](bin/BIN_INTERFACE.md):

```bash
sender --destination <IP_DESTINO> --port <PORTA>
receiver --address <IP_LOCAL> --port <PORTA>
```

- Os programas rodam sem interação, em primeiro plano, e abrem os próprios
  sockets.
- O receiver (e o sender, se usar `end: duration`) deve encerrar ao receber
  SIGTERM. Ser encerrado pelo testbed é normal e fica registrado como
  `ended_by: testbed`.
- Bibliotecas, chaves e certificados vão numa imagem própria
  `FROM drone:latest` (campo `nodes[].image`). A imagem precisa ter `ping`,
  usado na espera da malha (`drone:latest` já tem).
- Os exemplos `bin/sender` e `bin/receiver` não são um protocolo: o sender
  envia 1 byte por UDP a cada 50 ms até receber SIGTERM; o receiver só
  escuta.

## Pré-requisitos

- Linux com privilégios root;
- Docker funcional;
- Containernet/Mininet-WiFi importáveis no Python usado pelo projeto, com
  `wmediumd` e o comando `mn`;
- módulo BATMAN-adv no kernel do host;
- `tcpdump` no host;
- Python 3 com PyYAML;
- imagem `drone:latest` construída de `dockerfiles/Dockerfile.drone`.

## Build da imagem

```bash
scripts/build-docker.sh
```

Equivale a `docker build -t drone:latest -f dockerfiles/Dockerfile.drone .`.
A imagem é neutra (shell, Python, ferramentas de rede, `batctl`, `tcpdump`);
dependências de protocolo são responsabilidade de quem integra.

## Execução

```bash
sudo python3 scripts/run.py                         # usa scripts/config.yml
sudo python3 scripts/run.py outro-config.yml        # outro cenário
sudo python3 scripts/run.py --output-dir /outro/lugar
```

Saída típica:

```text
Run directory: .../logs/exp-08-mobilidade/2026-11-09_16-14-38
Mesh ready after 9.0s
Mobility: moved drone1 (365 position updates)
Routes/signal samples: 39
drone1->gcs: 407/698 delivered, median latency 0.445 ms
Experiment completed successfully.
Total time: 62.6s
```

Os arquivos gerados pertencem ao `root`; apagá-los exige `sudo`.

## Saídas

Cada execução grava numa pasta própria, que nunca é sobrescrita:

```text
logs/<experiment.id>/<AAAA-MM-DD_HH-MM-SS>/
    config.yml                      cópia exata do config usado
    metadata.json                   commit do testbed, sha256 dos binários e das trajetórias CSV,
                                    imagem, kernel, versão do BATMAN
    timing.json                     início, fim e duração de cada fase e do total
    capture-<nó>.pcap               todo o tráfego da bat0 de cada participante
    latency-<origem>-<destino>.csv  um pacote por linha: ID do IP, envio, chegada, latência, entregue/perdido
    positions.csv                   posição de cada nó a cada 0,1 s
    routes.csv                      tabela do BATMAN de cada nó a cada 1 s (próximo salto, TQ)
    signal.csv                      sinal, taxa e falhas do Wi-Fi por vizinho a cada 1 s
    trajectory-<nó>.csv             cópia do CSV de trajetória (quando usado)
    summary.json                    status, processos (exit code, stdout, stderr, ended_by),
                                    captura, latência/perda, mobilidade, avisos
    metrics.csv                     intervalos entre pacotes no sender (métrica antiga)
```

## Latência e perda

- **Latência de rede fim-a-fim**: da `bat0` do nó que envia à `bat0` do nó
  que recebe, por todos os saltos. Não inclui o tempo de processamento dentro
  dos programas ([por quê](docs/LATENCIA_REDE_X_PROGRAMA.md)).
- O mesmo pacote é reconhecido nas duas capturas pelo **ID do cabeçalho IP**
  (posto pelo kernel) junto com IPs, protocolo, portas e tamanho, com janela
  de 5 s. O conteúdo nunca é lido. Vale para qualquer pacote IPv4 (UDP, TCP,
  ICMP), nos dois sentidos, separado por fluxo.
- Métricas por sentido e por fluxo: enviados, entregues, perdidos, taxa de
  perda, maior rajada de perda, fora de ordem, duplicados, latência
  mín/mediana/média/p95 (n ≥ 20)/p99 (n ≥ 100)/máx e jitter.
- Em broadcast, o resultado sai por receiver.
- Os containers usam o relógio do host, então os horários dos PCAPs são
  comparáveis.

## Status e limitações

- `success`: processos e captura válidos e UDP na porta `protocol.port`
  observado no PCAP do sender;
- `inconclusive`: processos e captura válidos, mas nenhum UDP na porta
  `protocol.port` no sender;
- `failed`: falha de processo (inclusive `sender timed out` com
  `end: sender`) ou de captura. Se a execução para antes do protocolo (ex.:
  `network not ready`), não há `summary.json`; o `timing.json` marca a fase
  que falhou.

Interpretação honesta:

- perder pacotes **não** muda o status: é resultado do cenário;
- os campos `interval_*` do `metrics.csv` **não são latência**;
- exit `0` dos programas significa só encerramento local controlado;
- o critério do status ainda olha só UDP em `protocol.port` (um protocolo só
  TCP ou em outra porta sairia `inconclusive`); IPv6 é capturado, mas não
  entra na análise de latência;
- o wmediumd tem aleatoriedade sem semente: mesmo config, mesmo cenário, não
  os mesmos resultados;
- confirmado em execução real até agora só com os binários de exemplo (UDP);
- sem privilégios, Docker, módulos de kernel ou dependências do
  Mininet-WiFi, o cenário é **NÃO EXECUTADO**.

Lista completa em
[`docs/TESTBED_GUIA_COMPLETO.md`](docs/TESTBED_GUIA_COMPLETO.md), seção 12.

## Verificador de infraestrutura

```bash
sudo python3 scripts/validate_scenario.py
```

Checa os pré-requisitos, executa `scripts/run.py` uma vez e confere que a
plataforma montou o cenário, executou os programas, gerou PCAP/CSV/JSON
legíveis e não deixou containers residuais. Retorna `0` (plataforma ok), `1`
(falha operacional) ou `3` (**NÃO EXECUTADO** por pré-requisito ausente).
`PASSOU` não valida nenhum protocolo. Ainda não foi revisto para as
mudanças das semanas 3 e 4 (exige exit code `0` de todos os programas).

## Desenvolvimento

Módulos em `scripts/testbed/`:

| Módulo | Papel |
|---|---|
| `config.py` | Leitura e validação do YAML |
| `rundir.py` | Pasta por execução, cópia do config e das trajetórias |
| `metadata.py` | Versões e impressões digitais |
| `network.py` | Rede Containernet/Mininet-WiFi, BATMAN, espera da malha, limpeza |
| `runner.py` | Ciclo de vida de receivers e sender |
| `metrics.py` | tcpdump, `metrics.csv`, `summary.json` |
| `pcap.py` | Leitor de PCAP em Python |
| `latency.py` | Casamento de pacotes, latência e perda |
| `mobility.py` | Movimento dos nós e `positions.csv` |
| `observer.py` | `routes.csv` e `signal.csv` |
| `timing.py` | Tempos das fases |

Testes automáticos (não usam rede real; servem para detectar regressões, não
para validar o testbed, o que só a execução real faz):

```bash
python3 -m unittest discover -s tests
```

O código vendorizado em `third-party/containernet/` não é modificado.

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/TESTBED_GUIA_COMPLETO.md`](docs/TESTBED_GUIA_COMPLETO.md) | Visão completa: arquitetura, fases, config, saídas, medidas, histórico, resultados, limitações |
| [`docs/DECISOES.md`](docs/DECISOES.md) | Registro de decisões (contexto, motivo, onde no código, confirmações reais) |
| [`docs/LATENCIA_REDE_X_PROGRAMA.md`](docs/LATENCIA_REDE_X_PROGRAMA.md) | Por que medimos latência de rede |
| [`docs/HIPOTESES_2026-11-09.md`](docs/HIPOTESES_2026-11-09.md) | Hipóteses em aberto sobre os resultados |
| `docs/RELATORIO_*.md` | Relatórios de cada dia de trabalho |
| [`bin/BIN_INTERFACE.md`](bin/BIN_INTERFACE.md) | Contrato atual dos executáveis |
