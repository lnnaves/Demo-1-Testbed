# Contrato operacional dos binários

Este documento define a interface mínima dos binários `./sender` e
`./receiver`.

A implementação interna do protocolo e o formato das mensagens ficam a cargo
do usuário.

As regras de criação, ordenação, monitoramento e finalização dos processos
pertencem ao orquestrador e não fazem parte deste contrato.

## Receiver

### Execução

```bash
./receiver --address <endereço> --port <porta>
```

O receiver deve:

1. validar o endereço e a porta;
2. inicializar o protocolo;
3. criar e configurar o socket;
4. associar o socket ao endereço e à porta;
5. informar quando estiver pronto;
6. permanecer em execução enquanto aguarda mensagens;
7. registrar mensagens recebidas e erros.

### Estado READY

Quando estiver efetivamente disponível para receber mensagens, o receiver
deve escrever exatamente:

```text
READY
```

A linha deve ser escrita em `stdout` e disponibilizada imediatamente, sem
ficar retida em um buffer.

O receiver não deve emitir `READY` antes de concluir toda a inicialização
necessária.

Se a inicialização falhar, o receiver não deve emitir `READY` e deve encerrar
com um código de saída diferente de zero.

### Registro de recepção

Cada mensagem recebida deve gerar uma linha iniciada por `RECEIVED`.

Exemplo:

```text
RECEIVED bytes=1024 source=10.0.0.1
```

Erros devem gerar uma linha iniciada por `ERROR`.

Exemplo:

```text
ERROR message="receive failed"
```

## Sender

### Execução

```bash
./sender \
  --mode <unicast|broadcast> \
  --destination <endereço> \
  --port <porta> \
  --count <quantidade>
```

O sender deve:

1. validar os argumentos;
2. inicializar o protocolo;
3. executar a quantidade solicitada de transmissões;
4. registrar cada envio;
5. registrar erros;
6. encerrar depois de concluir as transmissões.

### Registro de envio

Cada envio realizado deve gerar uma linha iniciada por `SENT`.

Exemplo:

```text
SENT sequence=1 bytes=1024 destination=10.0.0.2
```

Erros devem gerar uma linha iniciada por `ERROR`.

Exemplo:

```text
ERROR sequence=2 message="send failed"
```

## Saída

Os eventos operacionais devem ser escritos em `stdout`.

Cada evento deve ocupar uma única linha. Os seguintes prefixos são
reservados:

- `READY`
- `RECEIVED`
- `SENT`
- `ERROR`

Informações adicionais para diagnóstico podem ser escritas em `stderr`.

## Encerramento

Os dois binários devem aceitar `SIGINT` e `SIGTERM`, liberar seus recursos e
encerrar sem exigir interação do usuário.

## Códigos de saída

| Código | Significado |
|-------:|-------------|
| `0` | Execução concluída ou encerramento controlado |
| `1` | Erro fatal durante a execução |
| `2` | Argumentos inválidos |