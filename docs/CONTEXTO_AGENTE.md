# Contexto de Inicialização do Agente de IA

Documento para abrir uma nova sessão de agente sobre o repositório
`lnnaves/Demo-1-Testbed` sem precisar reconstruir o contexto manualmente.
Cole/aponte este arquivo no início da sessão.

## 1. O que é o repositório

Plataforma de execução de protocolos em um cenário emulado de drones
(Containernet + Mininet-WiFi + BATMAN-adv + tcpdump). O produto é a
**plataforma**, não os executáveis de exemplo.

O testbed:

1. lê `scripts/config.yml`;
2. cria os containers de GCS e drones;
3. monta a rede ad hoc BATMAN-adv e atribui IPs;
4. executa **um único modo** (Unicast **ou** Broadcast);
5. inicia os receivers e depois o sender fornecidos pelo desenvolvedor;
6. captura tráfego com tcpdump;
7. gera PCAP, CSV e JSON;
8. limpa processos, containers e Mininet.

## 2. Princípios inegociáveis

- a plataforma é **agnóstica de protocolo**: não implementa, não interpreta e
  não valida semanticamente nenhum protocolo;
- não existe “protocolo de referência”: `bin/sender` e `bin/receiver` são
  **modelos mínimos substituíveis**;
- nada no testbed pode depender de payload, mensagens de stdout, contagem de
  pacotes enviados/recebidos ou de um `count` configurado;
- há **uma única** configuração persistente (`scripts/config.yml`) e **um único**
  script de execução (`scripts/run.py`);
- cada invocação executa somente o modo em `protocol.mode`; nunca alternar modos
  automaticamente nem gerar YAML temporário;
- `third-party/containernet/` é vendorizado e **não deve ser alterado**;
- honestidade nos resultados: tráfego no sender não comprova entrega; `interval_*`
  não é latência; exit `0` é só encerramento local; sem pré-requisitos reais o
  cenário é **NÃO EXECUTADO**, nunca `PASSOU`.

## 3. Contrato operacional mínimo dos executáveis

```bash
sender   --destination <IP_DESTINO> --port <PORTA>
receiver --address     <IP_LOCAL>   --port <PORTA>
```

- sem `--mode` e sem `--count`; o runner escolhe o destino conforme
  `protocol.mode`;
- executáveis ficam sob `bin/`, são executáveis, rodam em foreground e sem
  interação;
- a implementação abre e gerencia os próprios sockets;
- `stdout` = diagnóstico, `stderr` = erro;
- receiver responde a `SIGTERM`/`SIGINT`;
- executáveis não mexem em containers, interfaces, IPs, BATMAN-adv, rotas,
  captura ou métricas.

Detalhes em `bin/BIN_INTERFACE.md`.

## 4. Mapa do código

| Caminho | Papel |
|---|---|
| `scripts/config.yml` | Cenário: experiment, protocol, wireless, nodes, binaries, output |
| `scripts/run.py` | Orquestração por fases com cleanup garantido (`finally`) |
| `scripts/testbed/config.py` | `load_config`/`normalize_config` e todas as validações |
| `scripts/testbed/network.py` | `build_network`, atribuição de IP em `bat0`, `cleanup_mininet` |
| `scripts/testbed/runner.py` | `select_destination`, `execute_protocol`, `stop_receivers` |
| `scripts/testbed/metrics.py` | `Capture`, `analyze_pcap`, `write_csv`, `write_outputs` |
| `scripts/validate_scenario.py` | Validação **infraestrutural** (preflight + pós-execução) |
| `dockerfiles/Dockerfile.drone` | Imagem neutra `ubuntu:24.04` (sem ArduPilot, sem perf) |
| `scripts/build-docker.sh` | Gera `drone:latest` |
| `tests/` | Suíte unitária sem rede real |
| `logs/` | Saídas locais; só `logs/.gitkeep` é versionado |

## 5. Estado atual (o que já foi feito)

- removida toda a semântica de protocolo de referência: `protocol.count`,
  `sender --count`, payload `sequence=...;timestamp_ns=...`, linhas `TX`/`RX` e
  `Receiver stopped after <n> packets`;
- `bin/sender` e `bin/receiver` reduzidos a modelos mínimos com mensagens
  neutras (um datagrama mínimo apenas para smoke test);
- `runner.py` passa ao sender somente executável, `--destination`, destino,
  `--port`, porta; receivers continuam com `--address`/`--port`; receivers antes
  do sender;
- `scripts/validate_scenario.py` reescrito como verificação puramente
  infraestrutural (sem regex de stdout, sem contagem, sem semântica), com
  detecção de resíduos por `<container_name>` e `mn.<container_name>` e pelo
  label `com.containernet`;
- `Dockerfile.drone` migrado para `ubuntu:24.04`, sem ArduPilot e sem `perf`,
  mantendo `WORKDIR /workspace` e `CMD ["/bin/bash"]`;
- teste de artefatos de `logs/` corrigido para usar `git ls-files logs` em vez de
  exigir diretório fisicamente vazio;
- README e `bin/BIN_INTERFACE.md` reescritos para o discurso de plataforma;
- métricas preservadas: `packet_count`, `first_timestamp_seconds`,
  `last_timestamp_seconds`, `observed_duration_seconds`, `interval_mean_seconds`,
  `interval_median_seconds`, `interval_max_seconds`.

## 6. Próximo passo planejado

Integrar **PQ-EDHOC** como primeiro protocolo real, substituindo os modelos por
executáveis compatíveis com o contrato mínimo e adicionando bibliotecas,
certificados, chaves e dependências à imagem. O testbed continuará sem
interpretar o protocolo; a validação seguirá apenas infraestrutural.

## 7. Comandos úteis

```bash
# testes e sintaxe
python3 -m unittest discover -s tests
python3 -m py_compile scripts/run.py scripts/validate_scenario.py \
  scripts/testbed/*.py tests/*.py bin/sender bin/receiver

# imagem
scripts/build-docker.sh
docker image inspect drone:latest

# execução real (exige root, Docker, BATMAN-adv, Mininet-WiFi)
sudo python3 scripts/run.py
sudo python3 scripts/validate_scenario.py
```

## 8. Regras de trabalho para o agente

- mudanças cirúrgicas e no escopo pedido; nada de frameworks, plugins ou
  configuração dinâmica de argumentos sem pedido explícito;
- não reintroduzir semântica de protocolo no testbed, nos testes ou na
  documentação;
- não alterar `third-party/containernet/`;
- sempre rodar a suíte unitária e o `py_compile` antes de concluir;
- nunca afirmar sucesso end-to-end privilegiado em ambiente sem os
  pré-requisitos reais;
- documentação pública (README) não menciona histórico de PRs nem discussões
  internas.

## 9. Referência rápida

- Visão técnica completa e atual: `docs/RELATORIO_TECNICO.md`;
- Contrato dos executáveis: `bin/BIN_INTERFACE.md`;
- Uso e limitações: `README.md`.
