# Contrato dos Binários

O testbed executa dois binários disponibilizados em `bin/`:

```text
bin/sender
bin/receiver
```

Os IPs, porta, modo e quantidade são definidos pelo runner. Nenhum desses valores deve estar fixo nos binários.

## Receiver

```bash
receiver --address <IP_LOCAL> --port <PORTA>
```

Responsabilidades mínimas:

1. abrir e associar o socket ao endereço e porta recebidos;
2. inicializar o protocolo;
3. executar `rx(socket)`;
4. permanecer ativo até receber `SIGINT` ou `SIGTERM`.

O receiver não emite `READY`.

## Sender

```bash
sender --mode <unicast|broadcast> --destination <IP_DESTINO> --port <PORTA> --count <QUANTIDADE>
```

Responsabilidades mínimas:

1. abrir e configurar o socket;
2. inicializar o protocolo;
3. executar `tx(socket, destination, port, count)`;
4. encerrar após transmitir.

## Códigos de saída

| Código | Significado |
|---:|---|
| `0` | Sucesso ou encerramento controlado |
| `1` | Falha de execução |
| `2` | Argumentos inválidos |

O testbed monta `bin/` em `/opt/protocol/bin:ro`, inicia os processos, captura stdout/stderr e decide os endereços de destino.
