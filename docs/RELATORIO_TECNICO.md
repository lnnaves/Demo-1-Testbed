# Relatório Técnico — Demo-1-Testbed

## 1. O que é

O Demo-1-Testbed é uma **plataforma de execução de protocolos em um cenário
emulado de drones**. Ele monta uma rede ad hoc multi-hop com containers Docker
orquestrados por Containernet/Mininet-WiFi e roteamento BATMAN-adv, executa os
executáveis de protocolo fornecidos pelo desenvolvedor, captura o tráfego e
entrega três artefatos: PCAP, CSV e JSON.

A plataforma é **agnóstica de protocolo**: não implementa, não interpreta e não
valida semanticamente nenhum protocolo. Ela cuida exclusivamente de cenário,
rede, ciclo de vida dos processos, captura, artefatos e limpeza.

## 2. Fluxo de execução

```text
scripts/config.yml
        ↓
scripts/run.py
        ├── valida a configuração
        ├── cria os containers que representam GCS e drones
        ├── configura a rede ad hoc BATMAN-adv
        ├── atribui IPs
        ├── executa somente Unicast OU Broadcast
        ├── inicia receivers e sender fornecidos pelo desenvolvedor
        ├── captura tráfego com tcpdump
        ├── gera PCAP, CSV e JSON
        └── limpa processos, containers e Mininet
```

Cada invocação executa **um único modo**, o que estiver em `protocol.mode`.

## 3. Estrutura do repositório

| Caminho | Papel |
|---|---|
| `scripts/config.yml` | Única configuração persistente do cenário |
| `scripts/run.py` | Orquestrador: config → rede → captura → processos → artefatos → cleanup |
| `scripts/testbed/config.py` | Leitura e validação estrita do YAML |
| `scripts/testbed/network.py` | Criação da topologia Containernet/Mininet-WiFi e limpeza |
| `scripts/testbed/runner.py` | Ciclo de vida de receivers e sender, seleção de destino |
| `scripts/testbed/metrics.py` | Captura tcpdump, análise do PCAP, CSV e summary JSON |
| `scripts/validate_scenario.py` | Verificação **infraestrutural** do cenário configurado |
| `scripts/build-docker.sh` | Build da imagem `drone:latest` |
| `dockerfiles/Dockerfile.drone` | Imagem neutra dos nós |
| `bin/sender`, `bin/receiver` | Modelos mínimos substituíveis |
| `bin/BIN_INTERFACE.md` | Contrato operacional mínimo dos executáveis |
| `tests/` | Suíte unitária (sem rede real) |
| `logs/` | Saídas locais, não versionadas (exceto `.gitkeep`) |
| `third-party/containernet/` | Código vendorizado, não modificado |

## 4. Configuração (`scripts/config.yml`)

Blocos existentes hoje:

- `experiment`: `id`, `duration_seconds`, `receiver_startup_seconds`,
  `shutdown_timeout_seconds`;
- `protocol`: `mode` (`unicast` ou `broadcast`), `port`, `sender`, `receivers`;
- `wireless`: `ssid`, `mode`, `channel`, `ht_cap`, `noise_threshold_dbm`,
  `fading_coefficient`, `propagation_model`, `propagation_exponent`, `subnet`,
  `broadcast_ip`, `interface` (`bat0`);
- `nodes`: lista com `id`, `container_name`, `ip`, `position`, `image`,
  `memory`, `txpower`, `range`;
- `binaries`: `sender` e `receiver` (caminhos sob `bin/`);
- `output`: `pcap`, `csv`, `summary`.

Cenário padrão: `gcs` (192.168.123.1), `drone1` (192.168.123.2) e `drone2`
(192.168.123.3) em linha, com alcance de 25 m e espaçamento de 25 m, o que
produz um caminho multi-hop real entre os extremos.

### Validações aplicadas por `config.py`

- `experiment.id` e identificadores sem caracteres inválidos;
- tempos positivos;
- `protocol.mode` restrito a `unicast`/`broadcast`; porta em `1..65535`;
- Unicast exige exatamente um receiver; Broadcast exige pelo menos um;
- receivers sem duplicatas e sem conter o próprio sender;
- sender/receivers devem existir em `nodes`;
- `wireless.subnet` IPv4 e `broadcast_ip` coerente com a sub-rede;
- IPs de nós únicos, dentro da sub-rede e diferentes de rede/broadcast;
- `container_name` único, com ao menos um dígito (exigência do Mininet-WiFi) e
  cujo nome de interface `<container_name>-wlan0` não passe de 15 caracteres;
- `position` com três valores numéricos;
- binários existentes, executáveis e localizados sob `bin/`;
- caminhos de saída resolvidos a partir da raiz do projeto.

## 5. Rede emulada

`network.py` cria uma `Containernet` com `wmediumd` em modo `interference` e
modelo de propagação configurável. Cada nó é uma `DockerSta` privilegiada, com
imagem, limite de memória, posição, alcance e potência definidos no YAML, e com
`bin/` montado em `/opt/protocol/bin:ro`.

Os links são `adhoc` com `proto="batman_adv"`, ou seja, o próprio Mininet-WiFi
é o dono do ciclo de vida do BATMAN-adv. Após `net.start()`, o testbed apenas
atribui os IPs de aplicação na interface `bat0` de cada nó, com verificação de
sucesso por sentinela, já que `node.cmd()` não retorna código de saída.

A limpeza chama `net.stop()` e, em seguida, `mn -c`.

## 6. Execução dos processos

`runner.py`:

1. inicia **todos os receivers** definidos (apenas um, em Unicast), sempre antes
   do sender, com o comando
   `receiver --address <IP_LOCAL> --port <PORTA>`;
2. aguarda `receiver_startup_seconds` e verifica se algum receiver morreu na
   partida (nesse caso o sender nem chega a ser iniciado);
3. escolhe o destino por `select_destination()`: IP do único receiver em
   Unicast, `wireless.broadcast_ip` em Broadcast;
4. inicia o sender com `sender --destination <IP> --port <PORTA>`;
5. aplica timeout derivado de `duration_seconds`, com `terminate()` e depois
   `kill()` se necessário;
6. encerra os receivers de forma controlada; término solicitado pelo runner não
   é falha, saída espontânea é;
7. coleta `command`, `exit_code`, `stdout`, `stderr` e `duration_seconds` de
   cada processo.

O runner **não interpreta** stdout, payload ou sucesso semântico. O sender nunca
recebe `--mode` nem `--count`.

## 7. Captura e métricas

`metrics.py` inicia `tcpdump -i <interface> -w - udp port <porta>` no nó sender
antes de os processos começarem e grava diretamente o PCAP configurado. Na
parada, envia `SIGINT` (com fallback `terminate`/`kill`) e reanalisa o arquivo
com `tcpdump -tt -nn -r`.

Métricas passivas gravadas no CSV e no JSON:

- `packet_count`;
- `first_timestamp_seconds`;
- `last_timestamp_seconds`;
- `observed_duration_seconds`;
- `interval_mean_seconds`;
- `interval_median_seconds`;
- `interval_max_seconds`.

Limitações explícitas: são intervalos entre pacotes observados no sender —
não são latência, não são tempo de comunicação e não comprovam entrega aos
receivers. A captura desta versão filtra **UDP na porta configurada**.

## 8. Artefatos

- **PCAP**: tráfego capturado no nó sender;
- **CSV**: uma linha com as métricas acima;
- **JSON** (`summary`): `experiment`, `mode`, `status`, `process_status`,
  `capture_status`, `traffic_status`, `sender`, `receivers`, `capture`,
  `metrics`, `failures`, `warnings`, `outputs`.

Status agregado:

- `success`: processos válidos, captura válida e tráfego UDP observado;
- `inconclusive`: processos e captura válidos, sem tráfego filtrado observado;
- `failed`: falha de processo ou de captura.

Códigos de saída de `run.py`: `0` sucesso, `1` falha de execução, `2`
configuração inválida.

## 9. Contrato dos executáveis

```bash
sender   --destination <IP_DESTINO> --port <PORTA>
receiver --address     <IP_LOCAL>   --port <PORTA>
```

Requisitos: existir sob `bin/` e ser executável; rodar sem interação e em
foreground; abrir e gerenciar os próprios sockets; usar `stdout` para
diagnóstico e `stderr` para erro; receiver responder a `SIGTERM`/`SIGINT`;
saída `0` significa apenas encerramento local normal; saída diferente de zero
significa falha operacional. Os executáveis não configuram containers,
interfaces, IPs, BATMAN-adv, rotas, captura ou métricas.

`bin/sender` e `bin/receiver` incluídos são **modelos mínimos substituíveis**:
enviam/recebem um datagrama mínimo apenas para smoke test da infraestrutura. O
payload e as mensagens impressas não fazem parte de contrato algum.

## 10. Imagem de container

`dockerfiles/Dockerfile.drone` parte de `ubuntu:24.04` e instala apenas
`bash`, `ca-certificates`, `python3`, `iproute2`, `iputils-ping`, `iw`,
`wireless-tools`, `batctl`, `ebtables`, `kmod`, `procps`, `tcpdump` e
`libstdc++6`. Mantém `WORKDIR /workspace` e `CMD ["/bin/bash"]`. A imagem é
neutra: dependências de protocolo são adicionadas por quem integra sua
implementação. O build é feito por `scripts/build-docker.sh`, que gera
`drone:latest`.

## 11. Validação infraestrutural

`scripts/validate_scenario.py` exige root e verifica pré-requisitos: Docker
funcional, `mn`, `tcpdump`, `wmediumd`, imports de Containernet/Mininet-WiFi,
BATMAN-adv no host, imagens configuradas para os nós e diretórios de saída
utilizáveis. Depois executa `scripts/run.py <config>` **uma única vez** e
verifica:

- execução do único modo selecionado;
- sender igual ao nó configurado e receivers exatamente iguais aos configurados;
- destino do sender igual ao retornado por `select_destination()`;
- ausência de `--mode` e `--count` no comando do sender;
- presença de `--address` e `--port` nos comandos dos receivers;
- exit codes e resultados de processo coletados;
- coerência de `process_status`, `capture_status` e `traffic_status`;
- captura iniciada e válida;
- PCAP, CSV e JSON existentes e legíveis;
- campos obrigatórios do summary;
- ausência de containers Containernet residuais (`<container_name>` e
  `mn.<container_name>`, incluindo parados, sem confundir containers alheios).

Códigos: `0` plataforma executou e produziu artefatos; `1` falha operacional;
`3` cenário **NÃO EXECUTADO** por pré-requisito ausente. `PASSOU` refere-se
somente à infraestrutura.

## 12. Testes

Suíte unitária em `tests/`, sem rede real:

| Arquivo | Cobertura |
|---|---|
| `test_config.py` | Validação do YAML e normalização |
| `test_network.py` | Construção da topologia, IPs, cleanup |
| `test_runner.py` | Ordem, comandos, timeouts, falhas, cleanup de processos |
| `test_metrics.py` | Captura, análise de PCAP, CSV e summary |
| `test_run.py` | Orquestração e tratamento de erros por fase |
| `test_binaries.py` | Contrato operacional dos modelos em `bin/` |
| `test_validate_scenario.py` | Comportamento infraestrutural do validador |
| `test_repository_contract.py` | Contratos do repositório (config única, imagem neutra, `logs/`) |

Comandos:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile scripts/run.py scripts/validate_scenario.py \
  scripts/testbed/*.py tests/*.py bin/sender bin/receiver
```

## 13. Como usar

```bash
scripts/build-docker.sh          # gera drone:latest
$EDITOR scripts/config.yml       # define modo, nós, IPs, porta e saídas
sudo python3 scripts/run.py      # executa o modo configurado
sudo python3 scripts/validate_scenario.py   # verificação infraestrutural opcional
```

Para trocar de modo, edite `protocol.mode` e execute novamente. Para usar outro
protocolo, substitua `bin/sender` e `bin/receiver` por executáveis compatíveis
com o contrato mínimo e adicione as dependências na imagem.

## 14. Limites atuais

- observa apenas tráfego UDP na porta configurada;
- interface dos executáveis limitada a endereço/porta;
- tráfego observado no sender não comprova entrega;
- não há inferência de latência fim a fim;
- execução real exige root, Docker, kernel com BATMAN-adv e Mininet-WiFi;
- sem esses pré-requisitos, o resultado é **NÃO EXECUTADO**, nunca `PASSOU`.
