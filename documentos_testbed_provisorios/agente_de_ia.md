# Contexto para retomada do desenvolvimento do testbed

Última atualização: 14 de setembro de 2026

## Instruções para o agente

Este documento fornece o contexto necessário para continuar o desenvolvimento
do repositório:

```text
lnnaves/Demo-1-Testbed
```

Antes de sugerir ou realizar alterações:

1. leia este documento por completo;
2. consulte o estado atual dos arquivos no repositório;
3. não presuma que os códigos apresentados em conversas anteriores foram
   copiados integralmente;
4. compare as decisões documentadas aqui com a implementação atual;
5. mantenha o testbed independente de protocolos específicos;
6. não introduza dependências de EDHOC, PQ-EDHOC, initiator ou responder no
   núcleo do testbed;
7. preserve a simplicidade do contrato dos binários;
8. coloque no orquestrador todo o trabalho de medição e observabilidade.

Caso este documento e o código atual sejam diferentes, apresente a diferença
antes de propor mudanças.

## Objetivo do projeto

O projeto é um testbed genérico para avaliar protocolos de comunicação em uma
rede sem fio ad hoc/mesh formada por containers.

A arquitetura utiliza:

- Containernet;
- Mininet-WiFi;
- `DockerSta`;
- `wmediumd`;
- BATMAN-adv;
- containers representando drones e GCS;
- binários genéricos `sender` e `receiver`;
- cenários declarados em YAML;
- captura de tráfego e métricas pelo próprio testbed.

O objetivo principal é permitir a comparação do comportamento e do desempenho
de diferentes protocolos em condições de rede variadas.

As condições relevantes incluem:

- distância entre os nós;
- potência de transmissão;
- propagação;
- ruído;
- fading;
- interferência;
- perda resultante do meio;
- throughput;
- latência;
- retransmissões observáveis;
- mudanças de rota;
- futuramente, mobilidade.

## Princípio central

O testbed não deve conhecer a implementação interna do protocolo.

Qualquer protocolo pode ser utilizado, desde que o usuário entregue:

```text
bin/sender
bin/receiver
```

Esses arquivos devem cumprir o contrato definido em:

```text
bin/BIN_INTERFACE.md
```

O protocolo pode usar qualquer:

- linguagem;
- sistema de build;
- biblioteca;
- algoritmo;
- formato de mensagem;
- implementação interna.

O núcleo do testbed não deve compilar o protocolo.

## Decisão sobre `build-protocol.sh`

O arquivo original:

```text
scripts/build-protocol.sh
```

foi criado especificamente para EDHOC e PQ-EDHOC.

Ele conhece detalhes como:

- repositório `third-party/PQ-EDHOC`;
- `edhoc.mk`;
- `pq_edhoc.mk`;
- liboqs;
- `initiator`;
- `responder`;
- endereços incorporados ao código;
- Makefiles específicos.

Esse script não faz parte da arquitetura genérica.

Decisão:

- o testbed não precisa de um script genérico de build;
- o usuário é responsável por compilar seu protocolo;
- o resultado do build deve ser colocado em `bin/`;
- o script específico pode ser removido ou mantido apenas como exemplo de
  integração, com um nome que deixe sua natureza específica explícita;
- `run.py` e o YAML não devem depender desse script.

## Organização dos artefatos

A estrutura mínima é:

```text
bin/
├── BIN_INTERFACE.md
├── sender
└── receiver
```

O diretório também pode conter dependências de execução:

```text
bin/
├── sender
├── receiver
├── lib/
├── certificates/
├── keys/
├── config/
└── outros recursos
```

Todo o diretório `bin/` é montado em modo somente leitura dentro dos
containers, por padrão em:

```text
/opt/protocol/bin
```

Portanto:

```text
bin/sender
    → /opt/protocol/bin/sender

bin/receiver
    → /opt/protocol/bin/receiver
```

Os logs e arquivos temporários não devem ser gravados em `bin/`.

## Contrato mínimo dos binários

O contrato deve permanecer simples.

### Receiver

Forma de execução:

```bash
./receiver --address <endereço> --port <porta>
```

Responsabilidades mínimas:

1. validar os argumentos;
2. inicializar o protocolo;
3. criar e configurar os recursos de comunicação;
4. associar-se ao endereço e à porta;
5. ficar disponível para receber mensagens;
6. emitir exatamente `READY` em `stdout`;
7. permanecer em execução;
8. aceitar `SIGINT` e `SIGTERM`.

A linha deve ser:

```text
READY
```

Ela deve terminar com quebra de linha e não pode permanecer em buffer.

### Sender

Forma de execução:

```bash
./sender \
  --mode <unicast|broadcast> \
  --destination <endereço> \
  --port <porta> \
  --count <quantidade>
```

Responsabilidades mínimas:

1. validar os argumentos;
2. inicializar o protocolo;
3. realizar as transmissões;
4. encerrar quando terminar;
5. aceitar `SIGINT` e `SIGTERM`.

### Medições não pertencem ao contrato

O usuário não deve ser obrigado a produzir:

- timestamps;
- identificadores de sequência;
- contagens de bytes;
- eventos `SENT`;
- eventos `RECEIVED`;
- métricas;
- latência;
- perda;
- throughput;
- retransmissões.

O testbed deve realizar as medições externamente.

A única saída estruturada obrigatória é `READY`, pois o orquestrador precisa
saber quando pode iniciar os senders.

## Arquivo de cenário

O cenário principal está em:

```text
scripts/scenario.example.yml
```

O YAML foi reorganizado para conter os blocos:

```text
experiment
containers
execution
network
nodes
mobility
communication
readiness
termination
measurements
results
```

## Configuração do meio sem fio

Não utilizar Linux Traffic Control (`tc`) como mecanismo principal para
modelar enlaces da rede ad hoc.

Foi discutido que, para essa topologia ad hoc, o mecanismo apropriado é o
`wmediumd`.

O YAML antigo possuía:

```yaml
network:
  link:
    bandwidth_mbps: 10
    delay_ms: 5
    loss_percent: 0
```

Esse bloco foi considerado inadequado porque sugeria valores diretamente
aplicados, embora o `run.py` não os aplicasse e o modelo ad hoc não deva ser
tratado como um enlace ponto a ponto fixo.

A configuração atual usa:

```yaml
network:
  medium:
    mode: "interference"
    noise_threshold_dbm: -91
    fading_coefficient: 3

    propagation:
      model: "logDistance"
      exponent: 3.5
```

## Modos do `wmediumd`

### `interference`

É o único modo planejado como efetivamente implementado no estado atual.

Ele considera elementos como:

- posição;
- distância;
- potência de transmissão;
- modelo de propagação;
- ruído;
- fading;
- transmissões concorrentes;
- interferência.

Esse modo é adequado para a futura mobilidade dos nós.

### `snr`

O modo SNR foi discutido, mas ainda não deve ser ativado sem verificar a versão
e a API do Mininet-WiFi e do `wmediumd` instaladas no ambiente.

O modo SNR não é um coletor de métricas.

Ele recebe valores de SNR definidos para os pares de interfaces e usa esses
valores para modelar a entrega dos frames.

`interference` e `snr` são modos alternativos, não dois mecanismos que devem
ser executados simultaneamente para medir o experimento.

## Medições do testbed

As medições devem ser externas aos binários.

A implementação planejada utiliza:

### Captura de pacotes

Ferramenta:

```text
tcpdump
```

Formato de saída:

```text
PCAP
```

Análise posterior:

- Wireshark;
- `tshark`;
- `tcpdump -r`;
- scripts próprios de análise.

A captura inicial é feita em:

```text
bat0
```

Opcionalmente, pode ser feita em:

```text
<container_name>-wlan0
```

A captura em `bat0` representa a visão operacional utilizada pelo protocolo.

A captura na interface WLAN ajuda a analisar BATMAN-adv, encaminhamento e
comportamento mais próximo da camada sem fio, mas gera maior volume de dados.

### Contadores das interfaces

Devem ser coletados de:

```text
/sys/class/net/<interface>/statistics/
```

Contadores planejados:

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

As diferenças entre o início e o fim do experimento devem ser armazenadas.

Amostras periódicas também devem ser gravadas.

### BATMAN-adv

Devem ser capturados snapshots com comandos como:

```bash
batctl if
batctl neighbors
batctl originators
batctl statistics
```

A implementação deve tolerar diferenças entre versões do `batctl`.

### Processos

Para cada sender e receiver, registrar:

- comando;
- nó;
- função;
- instante de início;
- instante de `READY`, para receivers;
- instante de término;
- código de saída;
- sinal de encerramento;
- `stdout`;
- `stderr`.

### `wmediumd`

O `wmediumd` é principalmente o emulador do meio.

Ele não deve ser tratado como uma ferramenta que entrega automaticamente:

- latência fim a fim;
- throughput;
- perda da aplicação;
- retransmissões do protocolo.

Logs internos podem ajudar a investigar decisões de entrega, erro e condições
do meio, dependendo da versão instalada. A captura direta de logs ainda não
foi implementada porque o caminho e a integração precisam ser verificados no
ambiente real.

## Política para ausência de binários

O YAML possui:

```yaml
execution:
  enabled: true
  missing_binaries: "idle"
```

Políticas previstas:

### `idle`

A topologia permanece ativa durante `experiment.duration_seconds`.

O testbed:

- não executa sender ou receiver;
- não gera tráfego artificial;
- mantém o `wmediumd` e o BATMAN-adv ativos;
- captura tráfego de controle;
- coleta métricas básicas.

### `skip`

O testbed:

- cria e configura a topologia;
- coleta snapshots;
- não executa o protocolo;
- encerra sem aguardar a duração total.

### `fail`

A ausência de qualquer binário necessário encerra a execução com erro.

## Ordem de execução do protocolo

Quando os binários estão disponíveis:

1. criar a topologia;
2. configurar as interfaces;
3. iniciar o `wmediumd`;
4. configurar BATMAN-adv;
5. iniciar o `tcpdump`;
6. coletar métricas iniciais;
7. iniciar todos os receivers;
8. aguardar `READY` de todos;
9. iniciar os senders;
10. aguardar os senders ou o limite de duração;
11. encerrar os receivers com `SIGTERM`;
12. usar `SIGKILL` se necessário;
13. coletar métricas finais;
14. encerrar o `tcpdump`;
15. gravar o resumo;
16. parar a rede;
17. executar `mn -c`.

## Estado do `run.py`

Foi fornecida uma nova versão completa de:

```text
scripts/run.py
```

Essa versão foi planejada para implementar:

- carregamento e validação de YAML;
- defaults;
- expansão de `node_groups`;
- criação dinâmica dos containers;
- `wmediumd` em modo `interference`;
- BATMAN-adv;
- montagem de `bin/`;
- diretório gravável de resultados;
- inspeção dos binários;
- políticas `idle`, `skip` e `fail`;
- execução de receivers;
- espera por `READY`;
- execução de senders;
- captura de `stdout` e `stderr`;
- `tcpdump`;
- contadores de interfaces;
- snapshots do BATMAN-adv;
- `summary.json`;
- `--render`;
- `--cli`;
- `--telemetry`.

Entretanto, a versão final copiada pelo usuário precisa ser verificada.

Em uma mensagem anterior, o conteúdo exibido de `run.py` terminou de forma
truncada em:

```python
print("\
```

Na retomada, verificar imediatamente se o final real do arquivo contém:

```python
finally:
    print("\nCleaning the Mininet environment...")

    subprocess.run(
        ["mn", "-c"],
        check=False,
    )

return exit_code


if __name__ == "__main__":
    sys.exit(main())
```

Não assumir que o arquivo está sintaticamente completo sem consultá-lo.

## Limitações e pontos ainda não concluídos

### Mobilidade

O YAML possui um bloco de mobilidade, mas o `run.py` atual rejeita:

```yaml
mobility:
  enabled: true
```

Somente o modo estático está disponível.

### SNR

O modo SNR ainda não foi implementado.

Antes de implementá-lo:

1. identificar a versão instalada do Mininet-WiFi;
2. identificar a versão instalada do `wmediumd`;
3. verificar os símbolos disponíveis em `wmediumdConnector`;
4. consultar exemplos compatíveis com essa versão;
5. definir o schema da matriz de SNR no YAML.

### Intervalo das transmissões

O YAML contém:

```yaml
communication:
  transmission:
    interval_ms: 1000
```

Mas o contrato atual do sender não possui `--interval-ms`.

O valor ainda não é passado ao sender.

Decidir futuramente entre:

- remover `interval_ms` do YAML; ou
- adicionar `--interval-ms` como argumento opcional do contrato.

Não tornar esse argumento obrigatório sem avaliar o impacto na simplicidade do
contrato.

### Análise dos PCAPs

O `run.py` captura PCAPs, mas ainda não produz todas as métricas derivadas.

Ainda falta implementar análise com `tshark` ou outra biblioteca para calcular:

- pacotes por fluxo;
- bytes por fluxo;
- throughput;
- duração dos fluxos;
- intervalos;
- retransmissões TCP;
- perda inferida entre capturas;
- latência inferida por correlação;
- overhead de BATMAN-adv.

### Logs do `wmediumd`

A opção de captura pode existir no YAML, mas ainda não há uma implementação
confirmada para guardar o log do processo por execução.

### Opções reservadas

Alguns campos podem estar presentes no YAML sem implementação completa:

```text
measurements.process.enabled
measurements.batman.collect_periodically
measurements.active_probes.enabled
measurements.wmediumd.capture_log
```

Verificar o comportamento real do código antes de afirmar que essas opções já
funcionam.

### Diretórios possivelmente não utilizados

A estrutura de resultados pode criar diretórios como:

```text
tcpdump/
wmediumd/
```

Mesmo que a implementação atual grave o `stderr` do `tcpdump` em
`processes/` e não grave logs do `wmediumd`.

Isso deve ser revisado para evitar diretórios vazios ou inconsistentes.

## Próximos passos recomendados

Na próxima sessão:

1. consultar o conteúdo atual de:
   - `scripts/run.py`;
   - `scripts/scenario.example.yml`;
   - `bin/BIN_INTERFACE.md`;
2. executar validação de sintaxe:
   ```bash
   python3 -m py_compile scripts/run.py
   ```
3. executar:
   ```bash
   python3 scripts/run.py scripts/scenario.example.yml --render
   ```
4. corrigir qualquer incompatibilidade entre o YAML e o `run.py`;
5. executar um teste curto em modo `idle`;
6. validar os PCAPs, contadores, snapshots e `summary.json`;
7. criar binários mínimos temporários apenas para testar a orquestração;
8. testar `READY`, execução do sender e encerramento do receiver;
9. analisar falhas de lifecycle dos processos;
10. somente depois iniciar a análise automática dos PCAPs.

## Comandos de validação sugeridos

### Sintaxe

```bash
python3 -m py_compile scripts/run.py
```

### YAML normalizado

```bash
python3 scripts/run.py scripts/scenario.example.yml --render
```

### Execução ociosa curta

Configurar:

```yaml
experiment:
  duration_seconds: 10

execution:
  missing_binaries: "idle"
```

Executar:

```bash
sudo python3 scripts/run.py scripts/scenario.example.yml
```

### Inspeção dos resultados

```bash
find logs -type f -printf '%p %s bytes\n'
```

### Inspeção do resumo

```bash
find logs -name summary.json -print -exec cat {} \;
```

### Validação dos PCAPs

```bash
find logs -name '*.pcap' -print
tcpdump -nn -r <arquivo.pcap>
```

## Critério para preservar simplicidade

Ao propor uma nova obrigação para `sender` ou `receiver`, perguntar:

> O testbed consegue obter essa informação externamente?

Se a resposta for sim, a obrigação não deve ser adicionada ao contrato.

O contrato deve permanecer com o menor número possível de responsabilidades.