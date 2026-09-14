# Estado atual do Demo-1-Testbed

Última atualização: 14 de setembro de 2026

## Visão geral

O `Demo-1-Testbed` está sendo desenvolvido como uma plataforma genérica para
executar e avaliar protocolos de comunicação em redes sem fio ad hoc/mesh.

O cenário é formado por containers que representam drones e uma estação de
controle terrestre, conectados por Mininet-WiFi e BATMAN-adv.

A modelagem do meio sem fio é realizada pelo `wmediumd`.

A arquitetura pretendida é:

```text
scenario.example.yml
        │
        ▼
      run.py
        │
        ├── cria containers
        ├── configura interfaces sem fio
        ├── configura BATMAN-adv
        ├── inicia wmediumd
        ├── inicia medições
        ├── executa receivers
        ├── aguarda READY
        ├── executa senders
        ├── encerra os processos
        └── organiza os resultados
```

## Princípios definidos

### Independência de protocolo

O testbed não conhece a implementação interna dos protocolos.

O usuário fornece dois pontos de entrada:

```text
bin/sender
bin/receiver
```

Qualquer implementação pode ser usada, desde que cumpra o contrato operacional.

### Build externo

O testbed não compila protocolos.

O usuário é responsável por:

- implementar o protocolo;
- executar o build;
- empacotar as dependências;
- disponibilizar os artefatos em `bin/`;
- garantir compatibilidade com a imagem do container.

### Medições externas

Os binários não são responsáveis por calcular métricas.

O testbed coleta e analisa:

- tráfego;
- tempos;
- contadores;
- estados dos processos;
- informações da rede;
- informações do BATMAN-adv.

## Principais arquivos

### `bin/BIN_INTERFACE.md`

Define o contrato mínimo dos binários.

O documento estabelece:

- localização dos artefatos;
- organização de `bin/`;
- interface do receiver;
- interface do sender;
- estado `READY`;
- tratamento de sinais;
- códigos de saída;
- responsabilidades do testbed.

### `scripts/scenario.example.yml`

Define:

- identificação e duração;
- containers;
- política para ausência dos binários;
- rede;
- meio sem fio;
- nós;
- papéis;
- posições;
- comunicação;
- readiness;
- finalização;
- medições;
- diretório de resultados.

### `scripts/run.py`

É o orquestrador principal.

A versão planejada atual contém:

- carregamento do YAML;
- aplicação de defaults;
- validação;
- expansão de grupos de nós;
- criação dos containers;
- configuração da topologia;
- execução dos processos;
- coleta de dados;
- limpeza do ambiente.

### `dockerfiles/Dockerfile.drone`

A imagem já instala ferramentas importantes, incluindo:

```text
iproute2
iputils-ping
iw
wireless-tools
batctl
tcpdump
procps
perf
```

O `tcpdump` já está disponível para captura dentro dos containers.

## Contrato operacional

### Receiver

Execução:

```bash
./receiver --address <endereço> --port <porta>
```

O receiver deve:

1. validar seus argumentos;
2. inicializar o protocolo;
3. configurar os recursos de comunicação;
4. associar-se ao endereço e à porta;
5. ficar disponível para receber;
6. emitir `READY`;
7. permanecer em execução;
8. aceitar encerramento controlado.

Saída obrigatória:

```text
READY
```

### Sender

Execução:

```bash
./sender \
  --mode <unicast|broadcast> \
  --destination <endereço> \
  --port <porta> \
  --count <quantidade>
```

O sender deve:

1. validar seus argumentos;
2. inicializar o protocolo;
3. realizar as transmissões;
4. encerrar após concluir;
5. aceitar encerramento controlado.

### Obrigações que foram removidas

Os binários não precisam emitir:

```text
SENT
RECEIVED
ERROR
```

Também não precisam fornecer:

- números de sequência;
- timestamps;
- contagem de bytes;
- estatísticas;
- informações de perda;
- informações de latência.

O testbed captura `stdout` e `stderr`, mas não exige formato específico além de
`READY`.

## Organização do diretório `bin/`

Estrutura mínima:

```text
bin/
├── BIN_INTERFACE.md
├── sender
└── receiver
```

Estrutura ampliada possível:

```text
bin/
├── BIN_INTERFACE.md
├── sender
├── receiver
├── lib/
│   └── libprotocol.so
├── certificates/
├── keys/
├── config/
└── outros recursos
```

O diretório é montado nos containers como:

```text
/opt/protocol/bin
```

em modo somente leitura.

Um segundo volume gravável é montado para os resultados em:

```text
/opt/testbed/results
```

## Cenário atual

### Containers

Configuração padrão:

```yaml
containers:
  image: "drone:latest"
  binaries_directory: "/opt/protocol/bin"
  sender_binary: "sender"
  receiver_binary: "receiver"
```

O mapeamento é:

```text
Host                                      Container
bin/sender       → /opt/protocol/bin/sender
bin/receiver     → /opt/protocol/bin/receiver
bin/lib/...      → /opt/protocol/bin/lib/...
```

### Nós do exemplo

O cenário atual possui:

| Identidade | Container | Endereço | Papéis |
|---|---|---|---|
| `drone-1` | `dr1` | `192.168.123.1/24` | sender |
| `drone-2` | `dr2` | `192.168.123.2/24` | receiver |
| `drone-3` | `dr3` | `192.168.123.3/24` | receiver |
| `gcs` | `gcs0` | `192.168.123.5/24` | sender, receiver |

Os endereços operacionais são atribuídos a:

```text
bat0
```

Cada container também possui uma interface WLAN:

```text
dr1-wlan0
dr2-wlan0
dr3-wlan0
gcs0-wlan0
```

## Rede e `wmediumd`

### Topologia

A rede utiliza:

- modo ad hoc;
- BATMAN-adv;
- `wmediumd`;
- modelo de interferência;
- modelo de propagação `logDistance`.

Configuração definida:

```yaml
network:
  interface: "bat0"
  subnet: "192.168.123.0/24"

  wireless:
    ssid: "adhocNet"
    mode: "g"
    channel: 5
    bssid: "02:11:22:33:44:55"
    ht_cap: "HT40+"

  medium:
    mode: "interference"
    noise_threshold_dbm: -91
    fading_coefficient: 3

    propagation:
      model: "logDistance"
      exponent: 3.5
```

### Decisão sobre Traffic Control

Não foi implementado `tc` para impor:

- banda;
- atraso;
- perda.

A rede é ad hoc e não possui um Access Point fixo que sirva como segundo ponto
de referência para cada enlace.

Foi decidido usar o `wmediumd` para modelar o meio.

O bloco anterior:

```yaml
network:
  link:
    bandwidth_mbps: 10
    delay_ms: 5
    loss_percent: 0
```

foi removido porque não representava corretamente o comportamento da rede e
não estava sendo aplicado.

### Modo `interference`

O modo atual considera:

- posições;
- distância;
- potência de transmissão;
- propagação;
- ruído;
- fading;
- interferência entre transmissões.

Os valores de throughput, perda e latência são resultados do experimento, não
parâmetros impostos diretamente pelo YAML atual.

### Modo SNR

O modo SNR ainda não foi implementado.

Ele poderá ser usado futuramente para cenários em que a qualidade de cada
enlace seja definida por uma matriz de SNR.

Exemplo conceitual futuro:

```yaml
network:
  medium:
    mode: "snr"

    links:
      - source: "drone-1"
        destination: "drone-2"
        snr_db: 30
```

Antes de implementar, é necessário verificar as versões instaladas do
Mininet-WiFi e do `wmediumd`.

## Execução dos binários

### Fluxo normal

Quando os dois artefatos estão disponíveis:

```text
bin/sender
bin/receiver
```

o `run.py` deve:

1. criar os containers;
2. configurar a rede;
3. iniciar as capturas;
4. registrar métricas iniciais;
5. iniciar todos os receivers;
6. aguardar `READY`;
7. iniciar os senders;
8. aguardar a condição de término;
9. encerrar receivers e senders restantes;
10. registrar métricas finais;
11. parar as capturas;
12. salvar o resumo;
13. encerrar a topologia.

### Fluxos unicast

O YAML associa uma origem e um destino:

```yaml
communication:
  mode: "unicast"

  flows:
    - source: "drone-1"
      destination: "drone-2"
```

O orquestrador inicia:

```bash
/opt/protocol/bin/sender \
  --mode unicast \
  --destination 192.168.123.2 \
  --port 9000 \
  --count 10
```

### Broadcast

Para broadcast, o endereço pode ser informado ou derivado da sub-rede.

Exemplo:

```yaml
communication:
  mode: "broadcast"

  broadcasts:
    - source: "drone-1"
      destination: "192.168.123.255"
```

## Ausência dos binários

O cenário define:

```yaml
execution:
  enabled: true
  missing_binaries: "idle"
```

### `idle`

A rede permanece ativa durante a duração do experimento.

Nenhum programa substituto é iniciado e nenhum tráfego artificial de aplicação
é gerado.

As capturas podem registrar tráfego de controle do BATMAN-adv.

### `skip`

A topologia é criada, os snapshots são coletados e a execução é encerrada sem
aguardar a duração completa.

### `fail`

A ausência dos artefatos é tratada como erro fatal.

## Medições implementadas ou planejadas no `run.py`

### Captura de processos

Para sender e receiver:

- `stdout`;
- `stderr`;
- comando;
- instante de início;
- instante de `READY`;
- instante de término;
- código de saída;
- sinal usado para encerramento.

Arquivos esperados:

```text
processes/
├── drone-1.sender.1.to.drone-2.stdout.log
├── drone-1.sender.1.to.drone-2.stderr.log
├── drone-2.receiver.stdout.log
└── drone-2.receiver.stderr.log
```

### Captura PCAP

O `tcpdump` é iniciado antes dos binários.

Configuração padrão:

```yaml
measurements:
  packet_capture:
    enabled: true
    interfaces:
      - "bat0"
    snaplen: 0
    immediate_mode: true
    filter: ""
```

Arquivos esperados:

```text
pcaps/
├── drone-1.bat0.pcap
├── drone-2.bat0.pcap
├── drone-3.bat0.pcap
└── gcs.bat0.pcap
```

O nome lógico `wlan0` pode ser usado no YAML:

```yaml
interfaces:
  - "bat0"
  - "wlan0"
```

O `run.py` converte `wlan0` para o nome real de cada container.

### Contadores das interfaces

São coletados no início, periodicamente e no final:

```text
rx_bytes
tx_bytes
rx_packets
tx_packets
rx_dropped
tx_dropped
rx_errors
tx_errors
```

Arquivos esperados:

```text
counters/
├── initial.json
├── samples.jsonl
└── final.json
```

O `summary.json` contém as diferenças entre os snapshots inicial e final.

### BATMAN-adv

Snapshots esperados:

```text
batman/
├── initial.txt
└── final.txt
```

Informações tentadas:

- interfaces;
- vizinhos;
- originators;
- estatísticas.

Falhas em comandos opcionais do `batctl` não devem interromper o experimento.

### Telemetria

A opção:

```bash
--telemetry
```

inicia a telemetria de posição do Mininet-WiFi.

Ela ainda não substitui as demais métricas.

### Sondas ativas

Ferramentas como:

- `ping`;
- `iperf`;
- `iperf3`;

não são iniciadas durante os experimentos normais.

Elas geram tráfego e podem alterar o comportamento do meio.

O YAML mantém:

```yaml
active_probes:
  enabled: false
```

## Estrutura de resultados

Cada execução deve produzir uma estrutura semelhante a:

```text
logs/
└── unicast-demo-01/
    └── 20260914T120000Z/
        ├── scenario.yml
        ├── metadata.json
        ├── summary.json
        ├── processes/
        ├── pcaps/
        ├── counters/
        │   ├── initial.json
        │   ├── samples.jsonl
        │   └── final.json
        ├── batman/
        │   ├── initial.txt
        │   └── final.txt
        ├── tcpdump/
        └── wmediumd/
```

Alguns diretórios podem permanecer vazios na implementação atual.

## Métricas que podem ser extraídas

### Diretamente pelo orquestrador

- duração do experimento;
- tempo até `READY`;
- duração dos senders;
- códigos de saída;
- interrupções e timeouts;
- bytes TX/RX;
- pacotes TX/RX;
- drops e erros das interfaces;
- estado inicial e final da malha.

### A partir dos PCAPs

- quantidade de pacotes;
- quantidade de bytes;
- tráfego por endereço e porta;
- throughput médio;
- throughput ao longo do tempo;
- duração dos fluxos;
- intervalo entre pacotes;
- retransmissões TCP;
- ACKs duplicados;
- RTT estimado para TCP;
- overhead visível;
- diferenças entre capturas de origem e destino.

### Limitações das medições

Sem interpretar o formato interno do protocolo, o testbed pode medir a rede,
mas não necessariamente determinar:

- se uma operação semântica do protocolo foi concluída;
- qual mensagem da aplicação corresponde a outra mensagem;
- retransmissões internas da aplicação;
- sucesso criptográfico ou lógico do protocolo.

Isso é intencional para manter o contrato simples.

## O que o `wmediumd` fornece

O `wmediumd` modela a entrega dos frames de acordo com o modo escolhido.

No modo `interference`, ele ajuda a produzir efeitos relacionados a:

- qualidade do sinal;
- propagação;
- interferência;
- ruído;
- posição;
- potência.

Ele não produz automaticamente um relatório final contendo:

```text
latência média
throughput
perda fim a fim
retransmissões da aplicação
```

Essas métricas são derivadas pelo testbed a partir das capturas e dos
contadores.

Logs internos do `wmediumd` podem fornecer informações adicionais, mas a
captura desses logs ainda depende da versão instalada e não foi confirmada.

## Funcionalidades ainda pendentes

### Verificação completa da nova versão de `run.py`

O código fornecido durante a conversa precisa ser validado no ambiente real.

Verificações mínimas:

```bash
python3 -m py_compile scripts/run.py
python3 scripts/run.py scripts/scenario.example.yml --render
```

### Teste em modo idle

Executar com duração curta:

```yaml
experiment:
  duration_seconds: 10

execution:
  missing_binaries: "idle"
```

Depois:

```bash
sudo python3 scripts/run.py scripts/scenario.example.yml
```

Validar:

- `summary.json`;
- PCAPs;
- contadores;
- snapshots BATMAN-adv;
- limpeza dos containers.

### Teste dos processos

Ainda é necessário executar com binários reais ou mínimos para validar:

- início dos receivers;
- leitura de `READY`;
- timeout de readiness;
- início dos senders;
- encerramento dos senders;
- `SIGTERM` nos receivers;
- fallback para `SIGKILL`;
- captura completa de `stdout` e `stderr`.

### Análise automática de PCAPs

Ainda não foi implementada.

Uma próxima etapa deve decidir entre:

- chamar `tshark`;
- usar PyShark;
- usar Scapy;
- produzir scripts próprios para fluxos e correlação.

### Mobilidade

Ainda não implementada.

Modelos desejados:

```text
RandomWalk
RandomDirection
GaussMarkov
```

Deve ser integrada à API de mobilidade do Mininet-WiFi e ao modo
`interference` do `wmediumd`.

### Modo SNR

Ainda não implementado.

Requer investigação da instalação real antes de definir a API definitiva do
YAML.

### Atraso controlado

Ainda não existe um campo funcional para impor diretamente:

```text
delay_ms
```

A latência atualmente deve ser observada como resultado do meio e da pilha de
rede.

A implementação de atraso controlado por enlace deverá ser investigada na
versão do `wmediumd` utilizada pelo ambiente.

### Intervalo de transmissão

O campo:

```yaml
interval_ms: 1000
```

está reservado, mas não é passado ao sender porque o contrato atual não
possui esse argumento.

Esse ponto ainda precisa de decisão.

## Estado de maturidade

O projeto está na fase de implementação do primeiro fluxo completo.

### Definido conceitualmente

- arquitetura genérica;
- contrato dos binários;
- organização de `bin/`;
- schema principal do YAML;
- modo `interference`;
- políticas para ausência dos binários;
- estratégia de captura;
- estrutura de resultados.

### Implementado no código proposto

- topologia dinâmica;
- BATMAN-adv;
- montagem dos artefatos;
- execução de processos;
- readiness;
- modo idle;
- `tcpdump`;
- contadores;
- snapshots;
- resultados.

### Ainda não validado completamente

- lifecycle completo dos processos;
- PCAPs produzidos em todos os containers;
- comportamento de `tcpdump --immediate-mode`;
- encerramento dos processos dentro dos containers;
- integridade do `summary.json` em caminhos de erro;
- compatibilidade com as versões instaladas;
- execução com binários reais.

## Próxima etapa recomendada

A próxima etapa deve ser de validação e estabilização, não de adição de novas
funcionalidades.

Ordem recomendada:

1. verificar o conteúdo real dos três arquivos principais;
2. validar a sintaxe do Python;
3. validar o YAML normalizado;
4. executar por 10 segundos em modo idle;
5. inspecionar todos os resultados;
6. testar com sender e receiver mínimos;
7. corrigir problemas do gerenciamento de processos;
8. adicionar testes automatizados;
9. implementar análise básica dos PCAPs;
10. investigar SNR, logs do `wmediumd` e mobilidade.