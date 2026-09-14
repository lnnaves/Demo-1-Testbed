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


## Preparação e organização dos artefatos

O testbed não compila, empacota nem instala a implementação do protocolo.

Antes de executar um experimento, o usuário deve compilar seu protocolo e
disponibilizar os seguintes pontos de entrada:

```text
bin/sender
bin/receiver
```

Os dois arquivos devem:

- possuir permissão de execução;
- ser compatíveis com a arquitetura e o sistema operacional da imagem usada
  pelos containers;
- aceitar os argumentos definidos neste contrato;
- estar prontos para execução, sem exigir compilação ou instalação adicional
  dentro dos containers.

O sistema de build, a linguagem de implementação, os nomes internos e a
organização do código-fonte não fazem parte deste contrato.

### Conteúdo do diretório `bin/`

O diretório `bin/` representa o pacote executável do protocolo. Além dos pontos
de entrada obrigatórios `sender` e `receiver`, ele pode conter recursos
necessários em tempo de execução, incluindo:

- bibliotecas compartilhadas;
- certificados e chaves;
- arquivos de configuração;
- tabelas, modelos ou dados auxiliares;
- scripts de inicialização;
- binários auxiliares;
- outros recursos específicos da implementação.

Por exemplo:

```text
bin/
├── sender
├── receiver
├── lib/
│   └── libprotocol.so
├── certificates/
│   ├── sender.crt
│   └── receiver.crt
├── keys/
│   ├── sender.key
│   └── receiver.key
└── config/
    └── protocol.conf
```

As dependências necessárias para executar o protocolo devem seguir pelo menos
uma destas estratégias:

1. estar incorporadas aos executáveis;
2. estar previamente instaladas na imagem do container; ou
3. estar incluídas em `bin/` e ser localizadas pelos executáveis sem exigir
   instalação adicional.

Os executáveis são responsáveis por localizar corretamente seus arquivos
auxiliares. Recomenda-se usar caminhos relativos ao diretório do próprio
executável, em vez de depender do diretório de trabalho do processo.

Arquivos usados exclusivamente durante a compilação, como código-fonte,
arquivos-objeto, caches e diretórios temporários de build, não são necessários
para o testbed e recomenda-se mantê-los fora de `bin/`.

### Montagem nos containers

O testbed monta todo o conteúdo de `bin/` dentro de cada container. O ponto de
montagem é definido por `containers.binaries_directory` no arquivo de cenário
e, por padrão, corresponde a:

```text
/opt/protocol/bin
```

Assim, os pontos de entrada ficam disponíveis dentro dos containers como:

```text
/opt/protocol/bin/sender
/opt/protocol/bin/receiver
```

O diretório é montado em modo somente leitura. Portanto, os processos podem
ler executáveis, bibliotecas, certificados, chaves e configurações presentes
em `bin/`, mas não devem:

- modificar esses arquivos;
- armazenar logs no diretório;
- gravar resultados no diretório;
- depender da criação de arquivos temporários dentro dele.

Logs, resultados e estados mutáveis devem ser gravados em um diretório
gravável fornecido pelo ambiente ou pelo orquestrador.

### Configuração em tempo de execução

Endereços, portas, destinos, modo de transmissão e quantidade de mensagens
não devem estar fixados durante a compilação.

Esses valores são definidos pelo cenário e fornecidos em tempo de execução por
meio dos argumentos especificados neste contrato.

Certificados, chaves, arquivos de configuração e outros recursos específicos
da implementação podem ser armazenados em `bin/`. Entretanto, sua utilização
não deve impedir que `sender` e `receiver` aceitem os parâmetros operacionais
obrigatórios.

O testbed não interpreta os arquivos auxiliares e não conhece a implementação
interna do protocolo. Para o orquestrador, os únicos pontos de entrada
obrigatórios são:

```text
./sender
./receiver
```