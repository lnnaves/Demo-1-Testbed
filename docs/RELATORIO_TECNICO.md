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
  `shutdown_timeout_seconds`, `network_ready_timeout_seconds` (padrão 30),
  `drain_seconds` (padrão 1, aceita 0);
- `protocol`: `mode` (`unicast` ou `broadcast`), `port`, `sender`, `receivers`;
- `wireless`: `ssid`, `mode`, `channel`, `ht_cap`, `noise_threshold_dbm`,
  `fading_coefficient`, `propagation_model`, `propagation_exponent`, `subnet`,
  `broadcast_ip`, `interface` (`bat0`), `batman_bridge_loop_avoidance`
  (padrão `false`), `batman_orig_interval_ms` (padrão 1000);
- `nodes`: lista com `id`, `container_name`, `ip`, `position`, `image`,
  `memory`, `txpower`;
- `binaries`: `sender` e `receiver` (caminhos sob `bin/`).

Qualquer outro campo, em qualquer seção, é rejeitado com
`<seção>: unknown field '<nome>'`, inclusive campos removidos como `range` e
`output` (ver `docs/DECISOES.md`, D9). Os resultados não são configurados no
YAML: cada execução grava em `<--output-dir>/<experiment.id>/<data-hora>/`.

Cenário padrão (apenas um exemplo): `gcs` (192.168.123.1), `drone1`
(192.168.123.2) e `drone2` (192.168.123.3) em linha, em 0, 20 e 40 m. Com os
parâmetros de rádio padrão o enlace direto vai até ~60 m, então nesse exemplo
todos os nós são vizinhos diretos. O testbed não garante conectividade nem
multi-hop: o usuário posiciona os nós nas distâncias que quer avaliar e observa
o resultado.

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
imagem, limite de memória, posição e potência definidos no YAML, e com
`bin/` montado em `/opt/protocol/bin:ro`.

Não existe alcance fixo por nó. No modo interference o wmediumd calcula o
sinal de cada par de nós a partir de `txpower`, do modelo de propagação e da
distância, e descarta o que fica abaixo de `noise_threshold_dbm`. Medido com
os valores padrão (10 dBm, logDistance 3.5, -91 dBm, fading 3): ~-53 a -74 dBm
a 15 m, ~-68 a -82 dBm a 30 m, ~-82 a -89 dBm a 45 m e ~-90 dBm a 60 m. Por
isso o parâmetro `range` do Mininet-WiFi não é repassado.

Os links são `adhoc` com `proto="batman_adv"`, ou seja, o próprio Mininet-WiFi
é o dono do ciclo de vida do BATMAN-adv. Após `net.start()`, o testbed apenas
atribui os IPs de aplicação na interface `bat0` de cada nó, com verificação de
sucesso por sentinela, já que `node.cmd()` não retorna código de saída.

Logo após `net.start()`, cada nó recebe `batctl bl <0|1>` e `batctl it <ms>`.
O Bridge Loop Avoidance vem desligado por padrão: ligado, ele descarta
broadcasts (inclusive ARP) por ~30 s após o início enquanto procura outros
gateways de backbone, o que atrasava a malha em ~28 s sem haver bridge no
cenário. O intervalo de originator messages controla a rapidez com que o
BATMAN-adv reage à mobilidade: com 1000 ms ele não trocou para a rota via
relay antes de perder o enlace direto; com 200 ms trocou.

Antes de montar a rede, o testbed executa `mn -c` e remove containers
`mn.<container_name>` deixados por uma execução interrompida (o `mn -c` do
Mininet-WiFi não remove containers Docker).

Depois da atribuição de IPs, a fase `wait_network` espera a malha BATMAN-adv
convergir: o sender faz `ping` para cada receiver (espera de até 1 s pela resposta e
pausa de 0,2 s entre tentativas), até
todos responderem ou até `network_ready_timeout_seconds`. Em caso de timeout a
execução falha com `network not ready: <sender> could not reach <receiver>`.
Quando a malha está pronta, o cache ARP (`ip neigh flush`) de todos os nós é
limpo para que o protocolo não herde entradas criadas pela verificação.

A limpeza chama `net.stop()` e, em seguida, `mn -c`.

## 6. Execução dos processos

`runner.py`:

0. todos os processos são iniciados com `nsenter` nos namespaces do container
   (mount, UTS, IPC, rede), com a raiz e o ambiente do container. Não se usa o
   `popen` do Containernet porque o `docker exec -t` junta stderr ao stdout e
   não repassa sinais ao processo; com `nsenter`, `terminate()`/`kill()` chegam
   ao próprio executável;
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
6. aguarda `drain_seconds` após o fim do sender, para as últimas mensagens em
   trânsito chegarem, e então encerra os receivers de forma controlada; término solicitado pelo runner não
   é falha, saída espontânea é;
7. coleta `command`, `exit_code`, `stdout`, `stderr` e `duration_seconds` de
   cada processo.

O runner **não interpreta** stdout, payload ou sucesso semântico. O sender nunca
recebe `--mode` nem `--count`.

## 7. Captura e métricas

`metrics.py` inicia `tcpdump -i <interface> -w - udp port <porta>` no host,
dentro do namespace de rede do nó sender (`nsenter -t <pid> -n`), antes de os
processos começarem, e grava diretamente o PCAP configurado. O `popen` do
Containernet não é usado porque o `docker exec -t` aloca um TTY que corrompe a
saída binária e não repassa o `SIGINT` ao tcpdump. Na
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

Gravados por `scripts/testbed/rundir.py` em
`<--output-dir, padrão logs/>/<experiment.id>/<AAAA-MM-DD_HH-MM-SS>/`, com
sufixo `-2`, `-3`... se duas execuções começarem no mesmo segundo. A pasta é
criada logo após a leitura do config (fase `prepare_output`), e uma execução
que falha antes do fim deixa ao menos o `config.yml`.

- **`config.yml`**: cópia exata do config usado;
- **`capture.pcap`**: tráfego capturado no nó sender;
- **`metrics.csv`**: uma linha com as métricas acima;
- **`metadata.json`**: commit do testbed (e alterações não commitadas),
  sha256 dos binários, id das imagens Docker, kernel, versão do BATMAN-adv,
  versões de Mininet-WiFi/Containernet/Python, comando e usuário, e o aviso de
  que o fading do wmediumd não tem semente controlável;
- **`timing.json`**: início, fim, duração e status do experimento e de cada
  fase (de `load_config` a `cleanup`), gravado por último, inclusive em
  execuções que falham. Instantes em segundos Unix (mesma base do PCAP) e em
  hora local; durações pelo relógio monotônico. Os processos no
  `summary.json` também trazem `started_at`/`ended_at`;
- **`summary.json`**: `experiment`, `mode`, `status`, `process_status`,
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
