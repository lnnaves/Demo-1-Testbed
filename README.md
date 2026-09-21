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

## Desenvolvimento

Os módulos do núcleo ficam em `scripts/testbed/`:

- `config.py`: leitura e validação do YAML;
- `network.py`: criação/limpeza da rede Containernet/Mininet-WiFi;
- `runner.py`: lifecycle de receivers e sender;
- `metrics.py`: tcpdump, CSV e summary JSON.

Testes unitários não dependem de rede real:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile scripts/run.py scripts/testbed/*.py
```
