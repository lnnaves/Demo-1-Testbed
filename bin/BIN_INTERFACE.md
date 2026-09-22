# Contrato dos Binários

O testbed executa exatamente dois pontos de entrada disponibilizados em `bin/`:

```text
bin/sender
bin/receiver
```

Os IPs, porta, modo e quantidade são definidos pelo runner. Nenhum desses valores deve estar fixo nos binários.

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
7. no encerramento controlado, imprimir em stdout o total recebido e retornar `0`.

`rx(socket)` deve contar somente datagramas efetivamente retornados por `recvfrom()`. Timeouts usados para permitir encerramento por sinal não contam como recebimento.

O receiver não emite `READY`. Encerrar com `0`, inclusive após `SIGTERM` e com zero pacotes, significa somente encerramento controlado; não comprova que houve tráfego.

### Sender

```bash
sender --mode <unicast|broadcast> --destination <IP_DESTINO> --port <PORTA> --count <QUANTIDADE>
```

Responsabilidades mínimas:

1. validar os argumentos;
2. abrir um socket UDP/IPv4;
3. habilitar `SO_BROADCAST` somente no modo `broadcast`;
4. executar `tx(socket, destination, port, count)`;
5. transmitir exatamente `count` datagramas;
6. imprimir em stdout uma linha simples por envio;
7. encerrar após concluir as chamadas locais de envio e retornar `0`.

`tx(socket, destination, port, count)` deve realizar exatamente `count` chamadas locais de envio, salvo falha operacional. O retorno `0` do sender significa apenas que essas chamadas locais foram concluídas; não comprova entrega, recepção ou sucesso semântico do experimento.

## Payload dos binários de referência

Os binários de referência incluídos neste repositório usam payload textual simples:

```text
sequence=<N>;timestamp_ns=<TIMESTAMP>
```

`sequence` é crescente a partir de `1` e `timestamp_ns` é o timestamp de transmissão em nanossegundos. Esse formato mínimo existe para diagnóstico passivo e métricas futuras destes binários de referência. Ele não define nem limita o formato de payload de futuras implementações de protocolo, desde que a interface obrigatória de CLI e ciclo de vida acima seja respeitada.

## Códigos de saída

| Código | Significado |
|---:|---|
| `0` | Sender concluiu as chamadas locais de envio, ou receiver encerrou de forma controlada |
| `1` | Falha operacional, como erro ao abrir, configurar, associar, receber ou enviar pelo socket |
| `2` | Argumentos inválidos, incluindo erros do `argparse`, IPv4 inválido, porta fora de `1..65535` ou `count <= 0` |

`argparse` pode encerrar diretamente com código `2` para argumentos ausentes, tipos inválidos ou valores fora das opções declaradas.

## Fronteira de responsabilidade

Os binários não configuram rede, interfaces, IP, BATMAN-adv, rotas, captura ou métricas. Eles também não executam handshake, ACK, retransmissão, protocolo `READY`, múltiplas threads ou cálculo de latência/sucesso.

O testbed monta `bin/` em `/opt/protocol/bin:ro`, inicia os processos, decide endereços e modo, captura stdout/stderr e coleta PCAP/CSV/JSON. Stdout deve conter diagnóstico simples de operação normal; stderr deve ser usado para falhas. Rede e métricas pertencem ao testbed, não aos binários.
