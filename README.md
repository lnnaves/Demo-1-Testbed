# Demo-1-Testbed

Plataforma de execução de protocolos em um cenário emulado de drones, usando
Containernet, Mininet-WiFi, rede ad hoc BATMAN-adv, captura PCAP e relatórios
CSV/JSON.

O produto é a **plataforma (testbed)**, não os executáveis de exemplo. O
desenvolvedor substitui `bin/sender` e `bin/receiver` pela sua implementação,
configura o cenário em `scripts/config.yml`, executa o experimento e recebe
PCAP, CSV e JSON. A plataforma não implementa, interpreta nem valida nenhum
protocolo.

## Fluxo de execução

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

## Arquitetura: uma configuração, um modo por execução

Há uma única configuração persistente, `scripts/config.yml`. O usuário escolhe
exatamente um modo por execução em `protocol.mode`:

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
  port: 5000
  sender: drone1
  receivers:
    - gcs            # broadcast aceita vários receivers
```

## Integração do protocolo

Os arquivos `bin/sender` e `bin/receiver` são **modelos mínimos substituíveis**.
O desenvolvedor insere seu protocolo, bibliotecas, certificados, chaves e
dependências na imagem/estrutura apropriada e mantém apenas o contrato
operacional mínimo descrito em [`bin/BIN_INTERFACE.md`](bin/BIN_INTERFACE.md):

```bash
sender --destination <IP_DESTINO> --port <PORTA>
receiver --address <IP_LOCAL> --port <PORTA>
```

O runner fornece destino/endereço/porta; a implementação abre os próprios
sockets e executa sua lógica. O sender não recebe `--mode`: o runner escolhe o
destino conforme `protocol.mode`. O testbed não interpreta payload nem logs e
não valida o sucesso semântico do protocolo.

Nesta versão, a plataforma executa executáveis com interface de endereço/porta e
observa tráfego **UDP na porta configurada**.

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

A imagem é neutra: contém apenas shell, Python, ferramentas de rede,
BATMAN-adv e `tcpdump`. Dependências de protocolo são responsabilidade de quem
integra a implementação.

## Execução e validação

Ajuste `scripts/config.yml` (nós, IPs, WiFi, `protocol.mode`, porta e saídas) e
execute o modo configurado:

```bash
sudo python3 scripts/run.py
# opcionalmente, informando o caminho explícito da mesma configuração:
sudo python3 scripts/run.py scripts/config.yml
```

Para alternar entre Unicast e Broadcast, edite `protocol.mode` e execute
novamente.

Há um verificador opcional, **exclusivamente infraestrutural**, do modo já
selecionado:

```bash
sudo python3 scripts/validate_scenario.py
```

Ele exige root, checa os pré-requisitos, carrega a configuração e executa
`scripts/run.py` uma única vez. Nunca edita o YAML, nunca alterna de modo e
nunca gera YAML temporário. Confere somente que a plataforma montou o cenário,
executou o sender e os receivers configurados com os argumentos corretos,
coletou resultados de processo, iniciou uma captura válida, gerou PCAP/CSV/JSON
legíveis e não deixou containers residuais. Retorna `0` quando a plataforma
executou o cenário e produziu os artefatos, `1` em falha operacional e `3`
quando o cenário **NÃO FOI EXECUTADO** por pré-requisito ausente.

`PASSOU` significa somente que a plataforma montou, executou, coletou e limpou
corretamente; não significa que qualquer protocolo foi validado semanticamente.

## Saídas

As saídas padrão são gravadas em `logs/` e não são versionadas:

- PCAP: tráfego capturado no sender;
- CSV: métricas passivas observáveis da captura;
- `summary.json`: resumo operacional de processos, captura, métricas, falhas,
  warnings e caminhos.

Campos das métricas: `packet_count`, `first_timestamp_seconds`,
`last_timestamp_seconds`, `observed_duration_seconds`, `interval_mean_seconds`,
`interval_median_seconds` e `interval_max_seconds`.

## Status e limitações

- `success`: processos válidos, captura válida e tráfego UDP observado no sender;
- `inconclusive`: processos e captura válidos, mas nenhum tráfego filtrado observado;
- `failed`: falha de processo ou de captura.

Interpretação honesta dos resultados:

- o tráfego observado no sender **não comprova entrega** aos receivers;
- os campos `interval_*` são intervalos entre pacotes capturados e **não são
  latência nem tempo de comunicação**;
- exit `0` dos executáveis significa apenas encerramento local controlado;
- sem privilégios, Docker, módulos de kernel ou dependências do Mininet-WiFi, o
  cenário é **NÃO EXECUTADO**; não afirme `PASSOU` sem execução real;
- os próximos testes reais serão feitos diretamente com PQ-EDHOC, executáveis
  compatíveis com o contrato operacional mínimo.

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

O código vendorizado em `third-party/containernet/` não é modificado.
