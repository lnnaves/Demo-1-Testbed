# Testbed de protocolos para drones: guia completo

Estado em 09/11/2026, depois das semanas 1 a 4 do plano de execução.

Este documento explica o testbed para quem conhece só a ideia geral: um
ambiente que roda os programas (binários) de um protocolo em drones
emulados. Ele cobre o que o testbed faz, como faz, o que já foi validado em
execução real, o que ainda não foi, e por que cada decisão foi tomada.

Convenções:
- **REAL** = confirmado numa execução no computador da Laura (host), com os
  resultados conferidos nos arquivos gerados.
- **Hipótese** = explicação provável, ainda não verificada.
- **Dn** = número da decisão no registro [`DECISOES.md`](DECISOES.md), onde
  está a explicação completa.

---

## Sumário

1. [O que é e para que serve](#1-o-que-é-e-para-que-serve)
2. [Quem faz o quê: testbed × usuário](#2-quem-faz-o-quê-testbed--usuário)
3. [As peças: do host ao pacote](#3-as-peças-do-host-ao-pacote)
4. [Uma execução, passo a passo](#4-uma-execução-passo-a-passo)
5. [O arquivo de configuração](#5-o-arquivo-de-configuração)
6. [O que cada execução produz](#6-o-que-cada-execução-produz)
7. [Como as medidas funcionam](#7-como-as-medidas-funcionam)
8. [Mobilidade](#8-mobilidade)
9. [Princípios de trabalho](#9-princípios-de-trabalho)
10. [Histórico: o que foi feito em cada semana](#10-histórico-o-que-foi-feito-em-cada-semana)
11. [Resultados reais obtidos](#11-resultados-reais-obtidos)
12. [O que funciona, o que não, limitações](#12-o-que-funciona-o-que-não-limitações)
13. [Próximos passos](#13-próximos-passos)
14. [Como rodar](#14-como-rodar)
15. [Glossário](#15-glossário)
16. [Onde encontrar mais](#16-onde-encontrar-mais)

---

## 1. O que é e para que serve

O testbed responde a perguntas como: **"o meu protocolo continua
funcionando quando os drones se afastam? A partir de que distância ele
começa a perder mensagens? Quanto tempo as mensagens levam para chegar?"**

Para isso, ele monta, num único computador Linux, uma rede sem fio emulada
entre "drones", cada um num container Docker:
- o rádio Wi-Fi é emulado (sinal cai com a distância);
- os drones formam uma rede em malha (mesh) com o roteamento BATMAN-adv;
- o programa do protocolo roda dentro dos containers, sem modificação;
- o tráfego é gravado em cada drone e analisado no fim.

O testbed é **agnóstico de protocolo**: funciona com qualquer programa Linux
que se comunique por IP. Ele nunca lê o conteúdo das mensagens nem a saída
do programa para decidir nada. Testar o PQ-EDHOC, por exemplo, significa
trocar os binários de exemplo pelos do PQ-EDHOC.

Prioridades do projeto (prazo: ~18/12/2026):
1. unicast (um destinatário) e broadcast (todos) funcionando;
2. tempo de execução;
3. latência fim-a-fim;
4. entrega e perda de pacotes;
5. mobilidade.

---

## 2. Quem faz o quê: testbed × usuário

Decisão D1. A fronteira é rígida, para que o testbed sirva a qualquer
protocolo.

| O testbed cuida de | O usuário cuida de |
|---|---|
| Cenário (posições, movimento) | O programa do protocolo (binários) |
| Rádio emulado e roteamento BATMAN | Bibliotecas, chaves, certificados |
| Endereços IP e portas | Uma imagem Docker própria `FROM drone:latest`, se precisar de dependências |
| Esperar a rede ficar pronta | |
| Iniciar e encerrar os programas | |
| Gravar o tráfego (PCAP) e coletar saídas | |
| Calcular métricas, gerar relatórios | |
| Limpar o ambiente | |

**O que o programa do usuário precisa ter hoje** (contrato atual, que muda
na semana 6):
- rodar em Linux, sem interação, em primeiro plano;
- o **sender** aceitar `--destination <IP> --port <porta>`;
- o **receiver** aceitar `--address <IP> --port <porta>` e encerrar ao
  receber SIGTERM.

Na semana 6 o contrato vira um comando livre com marcadores (`{address}`,
`{destination}`, `{port}`...), para aceitar qualquer formato de argumentos.

Os binários de exemplo (`bin/sender` e `bin/receiver`) **não são um
protocolo**. Servem só para testar o testbed: o sender manda 1 byte (`\x00`)
a cada 50 ms até ser encerrado; o receiver só escuta.

---

## 3. As peças: do host ao pacote

```
 Host Linux (computador da Laura)
 ├── scripts/run.py ............ orquestrador (Python, roda com sudo)
 ├── tcpdump ................... um por participante, gravando a bat0
 ├── wmediumd .................. decide se um quadro de rádio chega
 └── Containers Docker (um por drone, imagem drone:latest)
     ┌──────────────────────────────────────────────┐
     │  programa do protocolo (sender ou receiver)  │
     │        │ socket UDP/TCP, IP 192.168.123.x     │
     │  bat0 ── interface do BATMAN-adv (IP do app) │  ← o PCAP é gravado aqui
     │        │                                      │
     │  wlan0 ─ Wi-Fi emulado (ad hoc)               │  ← mensagens do BATMAN (OGM)
     └──────────────────────────────────────────────┘
              ↕ rádio emulado (mac80211_hwsim + wmediumd)
```

| Peça | Papel |
|---|---|
| **Docker** | Cada drone é um container. A imagem `drone:latest` (Ubuntu 24.04 + `ping`, `iw`, `batctl`, `tcpdump`, Python) vem de `dockerfiles/Dockerfile.drone` |
| **Containernet** | Liga containers Docker ao Mininet (cada container vira um "nó" da rede) |
| **Mininet-WiFi** | Cria as interfaces Wi-Fi emuladas (`mac80211_hwsim`) e posiciona os nós |
| **wmediumd** (modo *interference*) | Calcula o sinal entre cada par de nós (potência, distância, modelo de propagação, *fading* aleatório) e descarta o que fica abaixo do limiar de ruído |
| **BATMAN-adv** | Roteamento mesh no kernel. Cada nó anuncia sua presença (OGMs); os nós descobrem vizinhos e rotas, inclusive por intermediários (multi-hop) |
| **bat0** | Interface virtual do BATMAN. O programa usa o IP dela (`192.168.123.x`) e não precisa saber da malha |
| **tcpdump** | Grava o tráfego da `bat0` de cada participante em PCAP |
| **Python (`scripts/`)** | Lê o config, monta a rede, roda os programas, move os nós, analisa e grava os resultados |

**Detalhe técnico que influenciou o projeto (D2).** O Containernet roda
comandos nos containers com `docker exec -t`. Isso corrompia o PCAP, juntava
stderr com stdout e não repassava o sinal de parada. Por isso o testbed roda
os programas com `nsenter` (entra direto nos *namespaces* do container) e o
`tcpdump` no host, dentro da rede do container. Efeito colateral: esses
processos ficam fora do limite de memória do container.

---

## 4. Uma execução, passo a passo

Comando: `sudo python3 scripts/run.py [config.yml]`. Cada fase é cronometrada
no `timing.json` (D10). Tempos da execução real de mobilidade (09/11):

| # | Fase | Tempo | O que acontece |
|---|---|---|---|
| 1 | `load_config` | ~0 s | Lê e valida o config; campo desconhecido = erro (D9) |
| 2 | `prepare_output` | ~0 s | Cria `logs/<experimento>/<data-hora>/` e copia o config para lá |
| 3 | `collect_metadata` | ~0 s | Grava versões: commit, hash dos binários, imagem, kernel, BATMAN (D11) |
| 4 | `prepare_host` | 3,4 s | Limpa restos de execuções anteriores (`mn -c`, containers `mn.*`) |
| 5 | `build_network` | 7,2 s | Sobe os containers, o rádio e o BATMAN; atribui os IPs |
| 6 | `wait_network` | 9,0 s | Pinga do sender a cada receiver até responderem; limpa o ARP (D3) |
| 7 | `start_capture` | 0,5 s | Inicia um tcpdump por participante |
| 8 | `execute_protocol` | 37,4 s | Receivers → 1 s → sender; move os nós e observa rotas/sinal; encerra; espera o *drain*; para os receivers |
| 9 | `stop_capture` | ~0 s | Para os tcpdumps |
| 10 | `analyze_network` | ~0 s | Casa os pacotes entre PCAPs; calcula latência e perda (D13) |
| 11 | `write_outputs` | ~0 s | Grava `summary.json`, CSVs |
| 12 | `cleanup` | 5,1 s | Desmonta a rede |

Total: 62,6 s, dos quais 35 s de protocolo. Numa execução curta, preparar e
desmontar ocupa a maior parte do tempo.

**Por que existe a espera da malha (D3).** Nas primeiras execuções nenhum
pacote chegava: o sender começava antes de o BATMAN descobrir os vizinhos.
Agora o testbed só inicia o protocolo quando um `ping` do sender alcança
cada receiver (limite: `network_ready_timeout_seconds`). Os pings acontecem
**antes** da captura, e o cache ARP é limpo em seguida. Ressalva (correção
na D3): o BATMAN tem um cache de ARP próprio (DAT) que não é limpo; o
primeiro pacote do protocolo pode não pagar o custo de uma resolução ARP.

**Por que a malha ficava pronta em ~28 s e agora em ~10 s (D4).** O BATMAN
liga por padrão o *Bridge Loop Avoidance*, que descarta broadcasts (inclusive
ARP) por ~30 s. Ele só serve quando a malha está ligada a uma rede cabeada;
foi desligado por padrão.

**Ordem dentro de `execute_protocol`:**
1. inicia os receivers e espera `receiver_startup_seconds` (1 s);
2. inicia o sender; nesse instante começa o relógio da mobilidade (t=0);
3. espera o sender terminar sozinho (`end: sender`) ou o encerra em
   `duration_seconds` (`end: duration`);
4. espera `drain_seconds` (1 s), para os pacotes em trânsito chegarem (D5);
5. encerra os receivers.

Ser encerrado pelo testbed é normal e fica registrado como
`ended_by: testbed`; terminar sozinho, como `ended_by: self`.

---

## 5. O arquivo de configuração

Um arquivo YAML descreve todo o experimento. Exemplo comentado (é o
`poc_mobilidade.yml` usado na validação da semana 4):

```yaml
experiment:
  id: exp-08-mobilidade            # nome da pasta de resultados
  end: duration                    # sender | duration (ver seção 4)
  duration_seconds: 35             # conta do início do sender
  receiver_startup_seconds: 1.0    # espera entre receivers e sender
  shutdown_timeout_seconds: 5.0    # tempo para um programa encerrar
  network_ready_timeout_seconds: 60.0  # limite da espera da malha
  drain_seconds: 1.0               # espera após o sender, antes de parar receivers

protocol:
  mode: unicast                    # unicast | broadcast
  port: 5000
  sender: drone1
  receivers: [gcs]                 # broadcast: um ou mais

wireless:
  ssid: meshNet
  mode: g
  channel: 5
  noise_threshold_dbm: -91         # abaixo disso o quadro não é recebido
  fading_coefficient: 3            # variação aleatória do sinal
  propagation_model: logDistance
  propagation_exponent: 3.5
  subnet: 192.168.123.0/24
  broadcast_ip: 192.168.123.255
  interface: bat0                  # onde o programa e a captura atuam
  batman_bridge_loop_avoidance: false  # D4
  batman_orig_interval_ms: 200     # anúncios do BATMAN; padrão 1000 (D6, D15)

nodes:
  - id: gcs                        # estação de solo
    container_name: gcs0
    ip: 192.168.123.1/24
    position: [0, 0, 0]            # metros
    txpower: 10                    # dBm
  - id: drone1
    container_name: dr1
    ip: 192.168.123.2/24
    position: [20, 0, 0]           # onde fica durante a espera da malha
    mobility:                      # opcional (semana 4)
      type: trajectory
      waypoints:                   # [segundo, x, y, z]
        - [0, 20, 0, 0]
        - [30, 150, 0, 0]
  - id: drone2
    container_name: dr2
    ip: 192.168.123.3/24
    position: [200, 0, 0]

capture:
  filter: ""                       # filtro tcpdump opcional; vazio = tudo (D12)

binaries:
  sender: bin/sender
  receiver: bin/receiver
```

Regras importantes:
- **Campo desconhecido é erro** antes de rodar (D9). Um `postion:` digitado
  errado não roda em silêncio com o valor padrão.
- **Não existe "alcance" configurável** (D7). Quem decide se dois nós se
  ouvem é o wmediumd, a partir de potência, distância e modelo de
  propagação. O antigo campo `range` não tinha efeito e foi removido.
- **O testbed não garante conectividade** (D7). O usuário escolhe as
  posições justamente para descobrir onde o protocolo deixa de funcionar.

---

## 6. O que cada execução produz

Cada execução cria `logs/<experiment.id>/<AAAA-MM-DD_HH-MM-SS>/` (D9), com
nomes sempre iguais, para comparar execuções e processar por script.

| Arquivo | Conteúdo | Desde |
|---|---|---|
| `config.yml` | Cópia exata do config usado | Semana 2 (D9) |
| `metadata.json` | Commit do testbed, sha256 dos binários, id da imagem, kernel, versão do BATMAN, pacotes | Semana 2 (D11) |
| `timing.json` | Início, fim e duração de cada fase e do total; gravado mesmo se a execução falhar | Semana 2 (D10) |
| `capture-<nó>.pcap` | Todo o tráfego da `bat0` de cada participante | Semana 2 (D12) |
| `summary.json` | Status, processos (exit code, stdout, stderr, horários, `ended_by`), captura, latência, mobilidade, avisos | Semanas 1–4 |
| `metrics.csv` | Intervalos entre pacotes no sender (métrica antiga; **não é latência**) | Semana 1 |
| `latency-<origem>-<destino>.csv` | Uma linha por pacote enviado: ID, horário de envio e de chegada, latência, entregue/perdido | Semana 3 (D13) |
| `positions.csv` | Posição de cada nó a cada 0,1 s | Semana 4 (D15) |
| `routes.csv` | Tabela do BATMAN de cada nó a cada 1 s | Semana 4 (D15) |
| `signal.csv` | Sinal, taxa, falhas do Wi-Fi por vizinho a cada 1 s | Semana 4 (D15) |

Exemplo de `latency-drone1-gcs.csv`:

```
flow,ip_id,ip_payload_bytes,sent_at,received_at,latency_ms,status,duplicates
udp 57919->5000,59920,9,1794247383.988397,1794247383.989270,0.873,delivered,0
udp 57919->5000,59955,9,1794247384.038542,1794247384.038778,0.236,delivered,0
```

Exemplo de `routes.csv` (o drone1 alcança o gcs diretamente, qualidade 154
de 255):

```
time,t,node,destination,best,next_hop,tq,last_seen_s,interface
1794251698.767292,,drone1,gcs,1,gcs,154,0.146,dr1-wlan0
```

**Status da execução** (`summary.json`):
- `success`: programas rodaram e encerraram como esperado, captura válida e
  tráfego observado;
- `inconclusive`: rodou, mas nenhum tráfego do protocolo foi visto;
- `failed`: programa falhou, captura inválida, rede não ficou pronta etc.

**Perder pacotes não é falha do testbed.** É resultado do cenário. A
análise de latência e perda nunca muda o status.

---

## 7. Como as medidas funcionam

### 7.1 Captura

- Um `tcpdump` por participante (sender e cada receiver), na `bat0` (D12).
- Grava **todo** o tráfego: filtrar na captura perde dados de forma
  irreversível; filtrar na análise não perde nada.
- Na `bat0` aparece o tráfego IP/ARP dos programas. As mensagens de
  roteamento do BATMAN passam na `wlan0` e não aparecem.
- Os containers usam o relógio do host, então os horários dos PCAPs de nós
  diferentes são comparáveis.

### 7.2 Que latência é medida

**Latência de rede fim-a-fim**: do momento em que o pacote passa pela `bat0`
do nó que envia até o momento em que passa pela `bat0` do nó que recebe,
atravessando todos os saltos do caminho.

**Não** é medida a latência de programa a programa (que incluiria o tempo
de processamento dentro do programa, como cifrar e decifrar). Os meios
práticos de medi-la exigiriam alterar o programa ou interpretar sua saída, o
que fere o princípio de não interferir no protocolo. Detalhes em
[`LATENCIA_REDE_X_PROGRAMA.md`](LATENCIA_REDE_X_PROGRAMA.md).

### 7.3 Como reconhecer o mesmo pacote nas duas capturas (D13)

Para calcular a latência, é preciso achar no PCAP do receiver **o mesmo
pacote** que saiu no PCAP do sender. Alternativas avaliadas:

| Opção | Problema |
|---|---|
| Casar por ordem (1º com 1º) | Erra quando há perda: liga pacotes diferentes |
| Hash do conteúdo (plano original) | Mensagens iguais têm o mesmo hash (o sender de exemplo manda sempre o mesmo byte; *heartbeats* reais também) |
| Só contar enviados × recebidos | Dá a perda aproximada, mas não dá latência, jitter nem rajadas |
| Testbed numerar os pacotes | Proibido: altera o tráfego do protocolo |
| **ID do cabeçalho IP** + IPs + protocolo + portas + tamanho | Escolhida |

O kernel Linux coloca um número de identificação (*IP ID*, 16 bits) em todo
pacote IP. O BATMAN transporta o pacote sem alterá-lo, então o mesmo
número aparece nos dois PCAPs (verificado: ID 27575 nos dois lados, e depois
20 de 20 pacotes casados).

Cuidados descobertos na prática:
- o ID **não sobe de 1 em 1**: o Linux soma um salto aleatório (~23 em
  média), então o número se repete a cada ~2.800 pacotes;
- por isso há uma **janela de 5 s**: um pacote do receiver só conta se
  chegar até 5 s depois do envio. Sem isso, um pacote perdido poderia ser
  ligado a outro pacote, minutos depois, com o mesmo ID, e a perda real
  ficaria escondida atrás de uma latência absurda;
- a ordem dos pacotes vem do horário de envio, não do ID.

O casamento não depende do protocolo: o ID é posto pelo kernel em qualquer
pacote IP. Funciona para UDP (inclusive CoAP, usado pelo PQ-EDHOC), TCP e
ICMP, **nos dois sentidos** (importante para protocolos de diálogo, como um
handshake). O testbed nunca lê o conteúdo.

A leitura dos PCAPs é feita em Python puro (`scripts/testbed/pcap.py`), sem
dependências novas. Para **visualizar** o tráfego, o usuário pode usar
Wireshark ou `tshark`.

### 7.4 Métricas calculadas

Por sentido (ex.: `drone1->gcs`) e por fluxo (protocolo + portas):

| Métrica | Significado |
|---|---|
| `sent`, `delivered`, `lost`, `loss_rate` | Enviados, entregues, perdidos, taxa de perda |
| `max_loss_burst` | Maior número de pacotes perdidos em sequência (*loss burst*; na RFC 3357, *loss period*). Dez perdas seguidas derrubam um handshake; dez espalhadas, talvez não |
| `out_of_order` | Pacotes que chegaram antes de outro enviado antes deles (troca de rota) |
| `duplicates` | O mesmo pacote chegando mais de uma vez |
| `latency_min/median/mean/max_ms` | Distribuição da latência. A mediana é o caso típico; a média sozinha engana (um pacote lento a puxa) |
| `latency_p95_ms`, `latency_p99_ms` | 95% / 99% dos pacotes chegaram em até esse tempo. Só calculados com n ≥ 20 / n ≥ 100; abaixo disso, vazios |
| `jitter_mean_ms` | Variação média da latência entre pacotes consecutivos; importa para controle e telemetria |

Aviso automático: se a latência máxima chegar a 50% do `drain_seconds`, o
testbed avisa que pacotes do fim podem ter sido contados como perdidos por
terem sido desligados cedo.

Limite da medida: um pacote que chega à `bat0` do receiver, mas que o
programa não consegue ler (por exemplo, buffer cheio), conta como
**entregue**. Ver isso exigiria olhar dentro do programa.

### 7.5 Tempos e metadados

- **Tempos (D10):** durações com relógio monotônico (imune a ajustes de
  relógio); instantes em segundos Unix, na mesma base dos PCAPs.
- **Metadados (D11):** permitem explicar por que dois resultados com o mesmo
  config diferem (testbed mudou? protocolo mudou? imagem refeita? kernel
  atualizado?).
- **Aleatoriedade:** o wmediumd sorteia o *fading* e a semente não pode ser
  fixada. Mesmo config = mesmo cenário, não os mesmos resultados. Por isso o
  plano prevê repetições com estatística.

---

## 8. Mobilidade

Implementada na semana 4 (D15).

- **Como descrever:** `nodes[].mobility` com `type: trajectory` e pontos
  `[segundo, x, y, z]` (no YAML ou num CSV). Entre pontos, linha reta; depois
  do último, parado. Sem `mobility`, o nó fica parado.
- **Quem move:** uma *thread* do testbed atualiza a posição a cada 0,1 s
  (`setPosition` do Mininet-WiFi); o wmediumd recalcula o sinal. O programa
  do protocolo não é tocado.
- **Quando:** t=0 é o início do sender. Durante a espera da malha, todos
  ficam na posição inicial (por isso o 1º ponto deve ser igual a
  `position`).
- **Observação, sem interferir:** a cada 1 s o testbed lê, em cada nó, a
  tabela de rotas do BATMAN (`batctl o`) e o estado do Wi-Fi por vizinho
  (`iw station dump`). São leituras de tabelas locais; nenhum pacote é
  enviado.
- **Intervalo do BATMAN:** com o padrão (1000 ms), o BATMAN demorou a trocar
  de rota quando um drone se afastava; com 200 ms, trocou a tempo (D6). O
  padrão continua 1000 ms; os exemplos com mobilidade usam 200 ms.

Com `positions.csv` e `latency-*.csv`, é possível dizer: "o pacote X se
perdeu quando o drone estava a 84 m". Hoje esse cruzamento é feito à mão; a
semana 5 o automatiza.

---

## 9. Princípios de trabalho

1. **Agnóstico de protocolo.** Nunca interpretar conteúdo nem saída dos
   programas. Nada do testbed depende de um protocolo específico.
2. **Não interferir na execução.** O testbed não altera o programa, não
   numera pacotes e não injeta tráfego durante o protocolo. Os pings da
   espera da malha acontecem antes da captura.
3. **Expor, não garantir.** O testbed mostra o que acontece (perda,
   atraso, rotas); não força a rede a funcionar.
4. **Honestidade.** Só execução real valida. Testes automáticos (com
   simulações e dados sintéticos) servem para avisar se algo que funcionava
   quebrou. Todo relato separa REAL de teste automático (D14). Hipóteses são
   marcadas como hipóteses, e erros viram "Correção" registrada.
5. **Registro.** Cada decisão vira uma entrada no `DECISOES.md` com
   contexto, motivo, onde está no código e implicações.
6. **Ciclo de trabalho.** Implementar e testar → um comando para o host →
   execução real → conferir os arquivos (inclusive de forma independente)
   → marcar no plano → commit.

Os testes automáticos ficam todos em `tests/` e serão removidos ao fim do
projeto. Nada do testbed depende dessa pasta (verificado; D14).

---

## 10. Histórico: o que foi feito em cada semana

O plano tem 6 semanas, a partir de 03/11/2026. Cada semana só fecha com
execução real confirmada.

### Semana 1: fazer funcionar (03–05/11) — concluída, na `main` (PR #20)

Problemas encontrados nas primeiras execuções reais e o que foi feito:

| Problema | Causa | Solução |
|---|---|---|
| PCAP corrompido; receiver não parava | `docker exec -t` (TTY) | Programas via `nsenter`; tcpdump no host (D2) |
| Nenhum pacote chegava | Protocolo começava antes de o BATMAN ter rotas | Espera da malha com ping + limpeza do ARP (D3) |
| Espera de ~28 s | *Bridge Loop Avoidance* descartando broadcasts | BLA desligado por padrão: ~10 s (D4) |
| Últimas mensagens perdidas | Receivers parados logo após o sender | `drain_seconds` (D5) |
| Rota não trocava com mobilidade | Anúncios do BATMAN a cada 1 s | `batman_orig_interval_ms` configurável (D6) |
| Restos de execuções anteriores | Containers e interfaces sobrando | Limpeza prévia automática |

Também: prova de conceito de mobilidade (`scripts/poc_mobility.py`), que
mostrou que mover um container muda o sinal e as rotas. Marco: unicast e
broadcast reais em `success`.

### Semana 2: saídas e tempo (05/11) — concluída, na `main` (PR #21)

- **Alcance (D7):** o campo `range` não tinha efeito; removido. Decidido que
  o testbed expõe o alcance em vez de garantir conectividade.
- **Pasta por execução (D9)** com cópia do config; validação estrita do
  config.
- **Tempos (D10):** `timing.json` com cada fase.
- **Metadados (D11):** `metadata.json`. Primeiro uso já revelou que a imagem
  Docker era mais antiga que o Dockerfile; a imagem foi refeita.
- **PCAP por participante (D12)**, capturando tudo, com filtro opcional.
  Primeira latência medida à mão: 1,73 ms.

### Semana 3: latência (08–09/11) — concluída, ainda sem PR

- Discussão longa sobre como casar pacotes (D13): hash × ordem × contagem ×
  ID do IP. Escolhido o ID do IP.
- Decidido medir latência de rede, não de programa a programa.
- Decidido analisar todo pacote IP entre participantes, nos dois sentidos (o
  "só UDP" do plano antigo era herança do hash).
- Métricas definidas, incluindo rajada de perda, jitter, fora de ordem,
  duplicados e percentis com amostra mínima.
- Leitor de PCAP e casamento em Python puro; CSV por sentido e bloco
  `latency` no `summary.json`.
- Regras dos testes automáticos (D14).
- Execuções reais de 20 m a 70 m: latência e entrega confirmadas em unicast
  e broadcast; **nenhuma perda** com nós parados.

### Semana 4: mobilidade (09/11) — concluída, ainda sem PR

- Trajetórias no config, movimento durante a execução, fim por duração,
  sender de exemplo contínuo (D15).
- Registro de posições, rotas do BATMAN e sinal de rádio.
- Execução real com o drone indo de 20 m a 150 m: **primeira perda real**,
  começando a 84 m.

---

## 11. Resultados reais obtidos

Todos com os binários de exemplo (1 byte por pacote).

### 11.1 Nós parados

| Execução | Distância | Entregues | Mediana | Máximo | Jitter |
|---|---|---|---|---|---|
| Unicast | 20 m | 20/20 | 0,240 ms | 1,166 ms | 0,157 ms |
| Broadcast → gcs | 20 m | 20/20 | 0,929 ms | 0,978 ms | 0,040 ms |
| Broadcast → drone2 | 20 m | 20/20 | 0,986 ms | 1,029 ms | 0,045 ms |
| Unicast | 50 m | 20/20 | 0,922 ms | 1,147 ms | 0,226 ms |
| Broadcast | 50 m | 20/20 | 0,938 ms | 4,191 ms | 0,549 ms |
| Broadcast | 58 m | 20/20 | 0,937 ms | 1,613 ms | 0,108 ms |
| Broadcast | 62 m | 20/20 | 0,924 ms | 3,639 ms | 0,399 ms |
| Broadcast | 70 m | 20/20 | 0,940 ms | 3,198 ms | 0,277 ms |
| Unicast, config padrão (10 s) | 20 m | 199/199 | 0,925 ms | 4,122 ms | 0,176 ms |

Com a distância, a piora apareceu como **atraso** (máximo e jitter sobem),
não como perda.

### 11.2 Drone se afastando (20 m → 150 m em 30 s)

698 enviados, **407 entregues (41,7% de perda)**, maior rajada de 76
pacotes.

| Distância | Entregues | Mediana | Máximo |
|---|---|---|---|
| 20–70 m | 229/229 | 0,23 ms | 4,4 ms |
| 70–80 m | 46/46 | 2,4 ms | 3,8 ms |
| 80–90 m | 37/46 | 2,7 ms | 111,6 ms |
| 90–100 m | 39/45 | 2,5 ms | 106,4 ms |
| 100–110 m | 25/46 | 60,6 ms | 111,5 ms |
| 110–120 m | 13/46 | 102,6 ms | 111,1 ms |
| 120–150 m | 14/137 | ~106 ms | 110,9 ms |
| 150 m (parado) | 2/101 | 107,7 ms | 110,1 ms |

Leitura: a latência sobe primeiro (~70 m); a perda começa a 84 m e cresce até
quase 100%; não há corte abrupto.

### 11.3 Hipóteses em aberto

Detalhes em [`HIPOTESES_2026-11-09.md`](HIPOTESES_2026-11-09.md).
- **Taxa do Wi-Fi:** ~0,9 ms quando o quadro sai numa taxa baixa (broadcast
  usa sempre a taxa básica; unicast quando o Wi-Fi escolhe 7,2 Mbit/s) e
  ~0,2 ms com 13 Mbit/s.
- **A perda vira atraso:** o Wi-Fi (unicast) e o BATMAN (broadcast) podem
  reenviar o que se perde. O contador de reenvios do Wi-Fi ficou sempre em 0
  no emulador, então isso não foi confirmado nem descartado.
- **~100 ms no fim da trajetória:** provável fila enquanto cada quadro
  espera tentativas de envio num enlace quase morto.

---

## 12. O que funciona, o que não, limitações

### Funciona (REAL)

- Unicast e broadcast com os binários de exemplo.
- Espera da malha, limpeza automática, pasta por execução, tempos,
  metadados.
- PCAP por participante.
- Latência e perda de rede por sentido, por receiver e por fluxo.
- Mobilidade por trajetória, fim por duração, registro de posições, rotas e
  sinal.

### Ainda não exercitado no real

- `end: sender` estourando o tempo (só em teste automático).
- Trajetória vinda de CSV (só em teste automático).
- Protocolo real (PQ-EDHOC) e tráfego TCP.
- Multi-hop de fato (nos testes até agora o enlace foi direto).

### Limitações conhecidas

| Limitação | Efeito |
|---|---|
| Aleatoriedade do wmediumd sem semente | Mesmo config, resultados diferentes; precisa de repetições |
| Espera da malha exige conectividade no início | Nós que começam fora de alcance → `network not ready` |
| BATMAN ainda converge durante a execução | A qualidade das rotas sobe depois da espera; pode afetar multi-hop |
| Cache DAT do BATMAN não é limpo | O primeiro pacote pode não pagar uma resolução ARP |
| Programas fora do cgroup do container | O limite de memória do nó não vale para eles |
| Sinal do `iw` é do último quadro recebido | Não desce abaixo de ~-90 dBm, mesmo sem enlace |
| `tx retries` sempre 0 no emulador | Não serve para estudar reenvios |
| Só nós participantes são capturados | Um relay não tem PCAP |
| Só IPv4; PCAP clássico | IPv6 e pcapng não são lidos |
| Precisão da espera da malha ~1,2 s | Cada tentativa de ping que falha espera 1 s |
| Contrato de argumentos fixo | Até a semana 6, o programa precisa aceitar `--destination/--address/--port` |
| Imagem do usuário precisa de `ping` | Usado na espera da malha; `FROM drone:latest` já tem |

---

## 13. Próximos passos

- **Semana 5:** latência e perda por janela de tempo, cruzadas com as
  posições (o testbed gerar sozinho a tabela da seção 11.2).
- **Semana 6:** contrato novo com marcadores no comando (`{address}`,
  `{destination}`, `{port}`, `{node_id}`...), pastas de dados e saída do
  protocolo, variáveis de ambiente; exemplo completo e guia "integre seu
  protocolo"; atualizar a documentação antiga; se der tempo, repetições
  com média, desvio e intervalo de confiança.
- Pendências: escolher o intervalo padrão do BATMAN para mobilidade;
  ajustar o validador (`validate_scenario.py`) ao `ended_by`; decidir o
  futuro do `metrics.csv` antigo.

---

## 14. Como rodar

Pré-requisitos no host: Linux com Docker, Containernet/Mininet-WiFi,
wmediumd, módulo BATMAN-adv, tcpdump, Python 3 com PyYAML.

```bash
# imagem dos drones (uma vez, ou quando o Dockerfile mudar)
bash scripts/build-docker.sh

# execução com o config padrão (scripts/config.yml)
sudo python3 scripts/run.py

# execução com outro config, guardando a saída da tela
sudo python3 scripts/run.py poc_mobilidade.yml 2>&1 | tee poc_mobilidade.log

# resultados em outra pasta base
sudo python3 scripts/run.py --output-dir /caminho/resultados
```

Saída típica na tela:

```
Run directory: .../logs/exp-08-mobilidade/2026-11-09_16-14-38
Mesh ready after 9.0s
Mobility: moved drone1 (365 position updates)
Routes/signal samples: 39
drone1->gcs: 407/698 delivered, median latency 0.445 ms
Experiment completed successfully.
Total time: 62.6s
```

Os arquivos da pasta pertencem ao `root` (a execução usa `sudo`); apagá-los
exige `sudo`.

---

## 15. Glossário

| Termo | Significado |
|---|---|
| **Ad hoc** | Modo Wi-Fi sem ponto de acesso: os nós falam diretamente entre si |
| **ARP** | Protocolo que descobre o endereço físico (MAC) a partir do IP |
| **BATMAN-adv** | Protocolo de roteamento mesh que roda no kernel Linux |
| **bat0** | Interface virtual criada pelo BATMAN; o programa usa o IP dela |
| **BLA** | *Bridge Loop Avoidance*, recurso do BATMAN desligado no testbed |
| **Broadcast** | Envio para todos os nós (`192.168.123.255`) |
| **DAT** | *Distributed ARP Table*, cache de ARP do próprio BATMAN |
| **Drain** | Espera depois do sender, para pacotes em trânsito chegarem |
| **Fading** | Variação aleatória do sinal de rádio |
| **Fluxo** | Conjunto de pacotes com mesmo protocolo e portas (ex.: `udp 37347->5000`) |
| **GCS** | *Ground Control Station*, estação de solo |
| **ID do IP** | Campo de 16 bits do cabeçalho IP, posto pelo kernel em cada pacote |
| **Jitter** | Variação da latência entre pacotes consecutivos |
| **Loss burst** | Sequência de pacotes perdidos seguidos |
| **Multi-hop** | Comunicação que passa por nós intermediários |
| **nsenter** | Comando que executa um programa dentro dos namespaces de um container |
| **OGM** | Mensagem de anúncio do BATMAN, enviada periodicamente |
| **p95 / p99** | Percentis: 95% / 99% dos valores ficam abaixo |
| **PCAP** | Arquivo com os pacotes capturados (Wireshark abre) |
| **SIGTERM** | Sinal de "encerre-se" enviado a um processo |
| **TQ** | *Transmit Quality*, qualidade de rota do BATMAN (0–255) |
| **Unicast** | Envio para um único destinatário |
| **wmediumd** | Emulador do meio sem fio: decide se cada quadro chega |

---

## 16. Onde encontrar mais

| Documento | Conteúdo |
|---|---|
| [`DECISOES.md`](DECISOES.md) | Todas as decisões (D1–D15) com contexto, motivo e confirmações |
| [`RELATORIO_2026-11-05.md`](RELATORIO_2026-11-05.md) | Relatório das semanas 1 e 2 |
| [`RELATORIO_2026-11-09.md`](RELATORIO_2026-11-09.md) | Relatório da semana 3 (latência) |
| [`RELATORIO_SEMANA4_2026-11-09.md`](RELATORIO_SEMANA4_2026-11-09.md) | Relatório da semana 4 (mobilidade) |
| [`LATENCIA_REDE_X_PROGRAMA.md`](LATENCIA_REDE_X_PROGRAMA.md) | Por que medimos latência de rede |
| [`HIPOTESES_2026-11-09.md`](HIPOTESES_2026-11-09.md) | Hipóteses sobre os resultados |
| `scripts/testbed/` | Código: `config.py`, `network.py`, `runner.py`, `metrics.py`, `pcap.py`, `latency.py`, `mobility.py`, `observer.py`, `timing.py`, `metadata.py`, `rundir.py` |
| `poc_*.yml` (raiz) | Configs das execuções de 09/11 |

Os documentos `README.md`, `RELATORIO_TECNICO.md`, `CONTEXTO_AGENTE.md` e
`bin/BIN_INTERFACE.md` estão parcialmente desatualizados e serão revistos na
semana 6. Onde divergirem, este guia e o `DECISOES.md` valem.
