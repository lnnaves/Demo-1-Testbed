# Contrato dos Binários

O testbed executa exatamente dois pontos de entrada disponibilizados em `bin/`:

```text
bin/sender
bin/receiver
```

O usuário define cenário, nós, IPs, porta, quantidade, sender e receivers somente em `scripts/config.yml`. O runner lê essa configuração e entrega os valores aos binários por argumentos. Os binários não leem o YAML e não possuem valores de rede fixos.

## Interface obrigatória de CLI e ciclo de vida

### Receiver

```bash
receiver --address <IP_LOCAL> --port <PORTA>
```

Responsabilidades mínimas:

1. validar os argumentos;
2. abrir um socket UDP/IPv4;
3. associar o socket ao IPv4 local e à porta recebidos;
4. executar `rx(socket)`;
5. permanecer recebendo até `SIGINT` ou `SIGTERM`;
6. imprimir em stdout uma linha simples por datagrama recebido;
7. no encerramento controlado, imprimir o total recebido e retornar `0`.

`rx(socket)` conta somente datagramas efetivamente retornados por `recvfrom()`. Timeouts usados para permitir encerramento por sinal não contam como recebimento.

O receiver não emite `READY`. Encerrar com `0`, inclusive após `SIGTERM` e com zero pacotes, significa somente encerramento controlado; não comprova que houve tráfego.

### Sender

```bash
sender --destination <IP_DESTINO> --port <PORTA> --count <QUANTIDADE>
```

Responsabilidades mínimas:

1. validar os argumentos;
2. abrir um socket UDP/IPv4;
3. permitir envio para um endereço de broadcast com `SO_BROADCAST`;
4. executar `tx(socket, destination, port, count)`;
5. transmitir exatamente `count` datagramas;
6. imprimir em stdout uma linha simples por envio;
7. encerrar após concluir as chamadas locais de envio e retornar `0`.

Unicast ou Broadcast **não faz parte do contrato do sender**. O modo é definido exclusivamente em `config.yml`. O runner converte essa escolha no destino correto: IP do receiver em Unicast ou endereço de broadcast da sub-rede em Broadcast.

`SO_BROADCAST` apenas permite que o destino recebido seja um endereço de broadcast; ele não transforma destinos unicast em broadcast.

O retorno `0` do sender significa somente que as chamadas locais de envio foram concluídas. Não comprova entrega ao receiver.

## Payload dos binários de referência

Os binários de referência incluídos neste repositório usam o payload textual:

```text
sequence=<N>;timestamp_ns=<TIMESTAMP>
```

`sequence` cresce a partir de `1` e `timestamp_ns` registra o instante local de transmissão em nanossegundos. Esse formato serve aos binários de referência e não obriga futuras implementações de protocolo, desde que respeitem a interface de CLI e o ciclo de vida.

## Códigos de saída

| Código | Significado |
|---:|---|
| `0` | Sender concluiu as chamadas locais de envio, ou receiver encerrou de forma controlada |
| `1` | Falha operacional ao abrir, configurar, associar, receber ou enviar pelo socket |
| `2` | Argumentos inválidos, incluindo IPv4 inválido, porta fora de `1..65535` ou `count <= 0` |

`argparse` pode encerrar diretamente com código `2` para argumentos ausentes ou tipos inválidos.

## Fronteira de responsabilidade

Os binários não configuram rede, interfaces, IP, BATMAN-adv, rotas, captura ou métricas. Também não executam handshake, ACK, retransmissão, protocolo `READY`, múltiplas threads ou cálculo de resultados.

O testbed monta `bin/` em `/opt/protocol/bin:ro`, inicia os processos, escolhe os endereços a partir de `config.yml`, captura stdout/stderr e coleta PCAP, CSV e JSON.
