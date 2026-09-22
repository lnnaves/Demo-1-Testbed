# Demo-1-Testbed

MVP modular do testbed para executar um experimento simples com Containernet,
Mininet-WiFi, rede ad hoc BATMAN-adv, captura PCAP e relatórios CSV/JSON.

## Arquitetura: uma configuração, um modo por execução

Há uma única configuração persistente, `scripts/config.yml`. O usuário escolhe
exatamente um modo por execução em `protocol.mode`, e o restante do fluxo é
automático:

```text
scripts/config.yml → protocol.mode → run.py → network.py → runner.py → metrics.py
```

- apenas o modo configurado é executado por invocação, nunca os dois em sequência;
- não existe outro YAML de cenário nem outro script de execução;
- em `unicast`, o runner exige exatamente um receiver configurado, inicia apenas
  esse receiver e usa o IP dele como destino do sender;
- em `broadcast`, o runner inicia todos os receivers configurados e usa
  `wireless.broadcast_ip` como destino;
- o Containernet/Mininet-WiFi monta a mesma infraestrutura ad hoc BATMAN-adv nos
  dois modos.

```yaml
protocol:
  mode: unicast      # ou: broadcast
  sender: drone1
  receivers:
    - gcs            # broadcast aceita vários receivers
```

## Pré-requisitos

- Linux com privilégios root;
- Docker funcional;
- Containernet/Mininet-WiFi importáveis no Python usado pelo projeto;
- `wmediumd` disponível;
- BATMAN-adv disponível no host para o lifecycle nativo do Mininet-WiFi;
- comandos `mn` e `tcpdump` disponíveis;
- imagem `drone:latest` construída de `dockerfiles/Dockerfile.drone`;
- `bin/sender` e `bin/receiver` executáveis conforme `bin/BIN_INTERFACE.md`.

## Build da imagem

```bash
scripts/build-docker.sh
```

O script equivale a:

```bash
docker build -t drone:latest -f dockerfiles/Dockerfile.drone .
```

## Execução e validação

Ajuste `scripts/config.yml` (nós, IPs, WiFi, `protocol.mode`, porta, quantidade e
saídas) e execute o modo configurado:

```bash
sudo python3 scripts/run.py
# opcionalmente, informando o caminho explícito da mesma configuração:
sudo python3 scripts/run.py scripts/config.yml
```

Para alternar entre Unicast e Broadcast, edite `protocol.mode` e execute
novamente.

Há um verificador opcional que valida **somente o modo já selecionado**:

```bash
sudo python3 scripts/validate_scenario.py
```

Ele exige root, checa os pré-requisitos, carrega a configuração e executa
`scripts/run.py` uma única vez. Nunca edita o YAML, nunca alterna de modo e
nunca gera YAML temporário. Confere o summary, a contagem final de cada receiver
configurado, o destino usado pelo sender, a ausência de `--mode` no comando do
sender, os status de processo/captura/tráfego e a leitura dos artefatos. Retorna
`0` apenas quando o cenário configurado passa; falha retorna `1` e pré-requisito
ausente retorna `3` (**NÃO EXECUTADO**).

## Saídas

As saídas padrão são gravadas em `logs/` e não são versionadas:

- PCAP capturado no sender;
- CSV com as métricas da captura;
- `summary.json` com status, processos, métricas e falhas.

## Status e métricas

- `success`: processos válidos, captura válida e tráfego UDP observado no sender;
- `inconclusive`: processos e captura válidos, mas nenhum tráfego filtrado observado;
- `failed`: falha de processo ou de captura.

Interpretação honesta dos resultados:

- o tráfego observado no sender **não comprova entrega** aos receivers;
- as métricas `interval_*` são intervalos entre pacotes capturados e **não são
  latência fim a fim**;
- exit `0` dos binários significa apenas encerramento controlado;
- ping e iperf não são critério de aprovação; a evidência vem do comportamento
  real de `bin/sender`/`bin/receiver` e dos artefatos PCAP/CSV/JSON;
- sem privilégios, Docker, módulos de kernel ou dependências do Mininet-WiFi, o
  cenário é **NÃO EXECUTADO**; não afirme `PASSOU` sem execução real.

## Desenvolvimento

Módulos do núcleo em `scripts/testbed/`:

- `config.py`: leitura e validação do YAML;
- `network.py`: criação/limpeza da rede Containernet/Mininet-WiFi;
- `runner.py`: lifecycle de receivers e sender;
- `metrics.py`: tcpdump, CSV e summary JSON.

Testes unitários, que não dependem de rede real:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile scripts/run.py scripts/validate_scenario.py scripts/testbed/*.py tests/*.py bin/sender bin/receiver
```
