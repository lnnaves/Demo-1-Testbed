# Demo-1-Testbed

MVP modular do testbed para executar um experimento simples com Containernet, Mininet-WiFi, rede ad hoc BATMAN-adv, captura PCAP e relatórios CSV/JSON.

## Uso

1. Disponibilize `bin/sender` e `bin/receiver` executáveis conforme `bin/BIN_INTERFACE.md`.
2. Ajuste `scripts/config.yml` com nós, IPs, WiFi, modo, porta, quantidade e saídas.
3. Execute com privilégios de rede:

```bash
sudo python3 scripts/run.py
```

Também é possível informar outro arquivo YAML:

```bash
sudo python3 scripts/run.py caminho/config.yml
```

As saídas padrão são gravadas em `logs/`: PCAP, CSV e `summary.json`.

## Validação real do MVP

Os cenários reproduzíveis ficam em:

- `scripts/config.unicast.yml`: Unicast, `drone1` enviando 5 datagramas para `gcs`;
- `scripts/config.broadcast.yml`: Broadcast, `drone1` enviando 5 datagramas para `gcs` e `drone2`.

Pré-requisitos para usar esses cenários como evidência real:

- Linux com privilégios root;
- Docker funcional;
- Containernet/Mininet-WiFi importáveis no Python usado pelo projeto;
- `wmediumd` disponível;
- BATMAN-adv disponível no host para o lifecycle nativo do Mininet-WiFi;
- comandos `mn` e `tcpdump` disponíveis;
- imagem `drone:latest` construída de `dockerfiles/Dockerfile.drone` ou imagem equivalente compatível.

Para construir a imagem local padrão:

```bash
docker build -t drone:latest -f dockerfiles/Dockerfile.drone .
```

Para executar cada cenário diretamente:

```bash
sudo python3 scripts/run.py scripts/config.unicast.yml
sudo python3 scripts/run.py scripts/config.broadcast.yml
```

Também há um verificador sequencial:

```bash
sudo python3 scripts/validate_scenarios.py
```

O verificador exige root explicitamente, checa os pré-requisitos, executa Unicast antes de Broadcast e para no primeiro erro. Ele valida os summaries produzidos, a contagem final de cada receiver no stdout dos binários de referência, o comando do sender, os status de processo/captura/tráfego e a existência/leitura dos artefatos PCAP/CSV/JSON. O retorno é `0` somente quando os dois cenários passam; pré-requisito ausente, cenário não executado, falha ou resultado inconclusivo retornam código não-zero.

Não use ping/iperf como critério de aprovação desses cenários. A validação funcional vem do comportamento real de `bin/sender`/`bin/receiver` e dos artefatos PCAP/CSV/JSON. Se o ambiente não oferecer privilégios, Docker, módulos de kernel ou dependências de Mininet-WiFi necessários, registre o bloqueio e marque o cenário como **NÃO EXECUTADO**; não afirme `PASSOU` sem execução real.

## Desenvolvimento

Os módulos do núcleo ficam em `scripts/testbed/`:

- `config.py`: leitura e validação do YAML;
- `network.py`: criação/limpeza da rede Containernet/Mininet-WiFi;
- `runner.py`: lifecycle de receivers e sender;
- `metrics.py`: tcpdump, CSV e summary JSON.

Testes unitários não dependem de rede real:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile scripts/run.py scripts/validate_scenarios.py scripts/testbed/*.py tests/*.py bin/sender bin/receiver
```
