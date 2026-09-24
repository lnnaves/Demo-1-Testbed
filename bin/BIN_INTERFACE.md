# Contrato operacional dos executáveis

O produto deste repositório é a **plataforma de execução de protocolos** no cenário emulado de drones. `bin/sender` e `bin/receiver` são apenas **modelos mínimos substituíveis**: existem para demonstrar como integrar um executável próprio e para permitir um smoke test da infraestrutura.

O testbed **não define** payload, algoritmo, handshake, formato de log ou critério de sucesso do protocolo. O desenvolvedor substitui os dois executáveis pela sua implementação (por exemplo, futuramente PQ-EDHOC) e adiciona bibliotecas, certificados, chaves e dependências na imagem do container.

```text
bin/sender
bin/receiver
```

O cenário, os nós, os IPs, a porta, o modo, o sender e os receivers são definidos somente em `scripts/config.yml`. O runner lê essa configuração e entrega os valores de rede por argumentos; os executáveis não leem o YAML e não têm valores de rede fixos.

## Interface mínima de CLI

### Receiver

```bash
receiver --address <IP_LOCAL> --port <PORTA>
```

### Sender

```bash
sender --destination <IP_DESTINO> --port <PORTA>
```

O sender **não recebe `--mode` nem `--count`**. O modo é escolhido em `config.yml` e o runner o converte no destino correto: o IP do receiver em Unicast ou o endereço de broadcast da sub-rede em Broadcast.

## Requisitos operacionais

- os arquivos existem sob `bin/` e são executáveis;
- executam sem interação humana e permanecem em foreground;
- recebem os parâmetros de rede fornecidos pelo runner;
- a própria implementação abre e gerencia seus sockets;
- `stdout` é diagnóstico normal e `stderr` é erro;
- o receiver responde a `SIGTERM`/`SIGINT` para encerramento controlado;
- os executáveis não configuram containers, interfaces, IPs, BATMAN-adv, rotas, captura ou métricas.

## Códigos de saída

| Código | Significado |
|---:|---|
| `0` | Encerramento local normal do processo — **não** é sucesso semântico do protocolo |
| diferente de `0` | Falha operacional |

`argparse` (ou equivalente) pode encerrar diretamente com código `2` para argumentos ausentes ou inválidos.

## Limitação desta versão

A plataforma executa executáveis com interface de **endereço/porta** e observa tráfego **UDP na porta configurada** na captura. O testbed não interpreta o conteúdo enviado nem as mensagens impressas pelos processos.

## Fronteira de responsabilidade

O testbed monta `bin/` em `/opt/protocol/bin:ro`, inicia os processos (receivers antes do sender), escolhe os endereços a partir de `config.yml`, coleta stdout/stderr/exit code/duração e gera PCAP, CSV e JSON.
