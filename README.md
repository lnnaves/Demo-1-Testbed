# Demo-1-Testbed

MVP modular do testbed para executar um experimento simples com Containernet, Mininet-WiFi, rede ad hoc BATMAN-adv, captura PCAP e relatórios CSV/JSON.

## Arquitetura: uma única configuração, um único modo por execução

Há uma única configuração persistente, `scripts/config.yml`. O usuário escolhe
exatamente um modo por execução em `protocol.mode`, e o restante do fluxo é
automático:

```text
scripts/config.yml
       ↓
protocol.mode
       ↓
unicast OU broadcast
       ↓
run.py → network.py → runner.py → metrics.py
```

- **apenas um modo é executado por invocação** — nunca os dois em sequência;
- mudar `protocol.mode` muda o cenário da **próxima** execução, não da atual;
- o usuário não escolhe outro script nem outro arquivo YAML;
- o runner (`scripts/testbed/runner.py`, função `select_destination`) escolhe
  automaticamente o destino do sender a partir do modo configurado;
- o Containernet/Mininet-WiFi monta sempre a mesma infraestrutura de rede
  ad hoc BATMAN-adv, independentemente do modo;
- a validação real de qualquer um dos modos exige o ambiente privilegiado
  listado abaixo.

Exemplo mínimo para Unicast (`drone1` envia somente para `gcs`):

```yaml
protocol:
  mode: unicast
  sender: drone1
  receivers:
    - gcs
```

Exemplo mínimo para Broadcast (`drone1` envia para todos os receivers
configurados):

```yaml
protocol:
  mode: broadcast
  sender: drone1
  receivers:
    - gcs
    - drone2
```

Em Unicast, o runner exige exatamente um receiver configurado e usa o IP
desse receiver como destino. Em Broadcast, o runner inicia todos os
receivers configurados e usa `wireless.broadcast_ip` como destino.

## Uso

1. Disponibilize `bin/sender` e `bin/receiver` executáveis conforme `bin/BIN_INTERFACE.md`.
2. Ajuste `scripts/config.yml` com nós, IPs, WiFi, `protocol.mode` (`unicast` ou `broadcast`), porta, quantidade e saídas.
3. Execute com privilégios de rede:

```bash
sudo python3 scripts/run.py
```

Também é possível informar explicitamente o mesmo arquivo:

```bash
sudo python3 scripts/run.py scripts/config.yml
```

Não existe outro YAML de cenário: para alternar entre Unicast e Broadcast,
edite `protocol.mode` em `scripts/config.yml` e execute novamente.

As saídas padrão são gravadas em `logs/`: PCAP, CSV e `summary.json`.

## Validação real do MVP

Pré-requisitos para validar o modo configurado como evidência real:

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

Para executar o modo já configurado em `scripts/config.yml`:

```bash
sudo python3 scripts/run.py
```

Há também um verificador opcional que valida **somente o modo já
selecionado** em `scripts/config.yml` (ou em outro caminho informado
explicitamente):

```bash
sudo python3 scripts/validate_scenario.py
```

O verificador exige root explicitamente, checa os pré-requisitos, carrega
`scripts/config.yml` e executa `scripts/run.py` **uma única vez**. Ele nunca
edita o YAML, nunca alterna automaticamente entre Unicast e Broadcast e nunca
gera arquivos YAML temporários para mudar o modo. A validação confere o
summary produzido, a contagem final de cada receiver configurado no stdout
dos binários de referência, o destino usado pelo sender (IP do único
receiver em Unicast, ou `wireless.broadcast_ip` em Broadcast), a ausência de
`--mode` no comando do sender, os status de processo/captura/tráfego e a
existência/leitura dos artefatos PCAP/CSV/JSON. O retorno é `0` somente
quando o cenário configurado passa; pré-requisito ausente, falha ou
resultado inconclusivo retornam código não-zero, distinguindo
PASSOU/FALHOU/NÃO EXECUTADO.

Não use ping/iperf como critério de aprovação desse cenário. A validação
funcional vem do comportamento real de `bin/sender`/`bin/receiver` e dos
artefatos PCAP/CSV/JSON. Se o ambiente não oferecer privilégios, Docker,
módulos de kernel ou dependências de Mininet-WiFi necessários, registre o
bloqueio e marque o cenário como **NÃO EXECUTADO**; não afirme `PASSOU` sem
execução real.

## Desenvolvimento

Os módulos do núcleo ficam em `scripts/testbed/`:

- `config.py`: leitura e validação do YAML;
- `network.py`: criação/limpeza da rede Containernet/Mininet-WiFi;
- `runner.py`: lifecycle de receivers e sender;
- `metrics.py`: tcpdump, CSV e summary JSON.

Testes unitários não dependem de rede real:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile scripts/run.py scripts/validate_scenario.py scripts/testbed/*.py tests/*.py bin/sender bin/receiver
```
