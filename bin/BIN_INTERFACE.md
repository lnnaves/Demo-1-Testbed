# Contrato operacional dos binários

Este documento define a interface mínima dos binários `sender` e `receiver`
executados pelo testbed.

A implementação interna do protocolo, o formato das mensagens e as tecnologias
utilizadas ficam a cargo do usuário.

A criação dos processos, a captura do tráfego, a coleta das métricas e a
finalização do experimento são responsabilidades do testbed.

## Preparação dos artefatos

O testbed não compila, empacota nem instala a implementação do protocolo.

Antes de executar um experimento, o usuário deve compilar o protocolo e
disponibilizar os seguintes pontos de entrada:

```text
bin/sender
bin/receiver
```

Os dois arquivos devem:

- possuir permissão de execução;
- estar prontos para execução;
- ser compatíveis com a arquitetura e o sistema operacional da imagem usada
  pelos containers;
- aceitar os argumentos definidos neste documento;
- não exigir compilação ou instalação adicional durante o experimento.

O sistema de build, a linguagem de implementação e a organização do
código-fonte não fazem parte deste contrato.

## Conteúdo do diretório `bin/`

Além de `sender` e `receiver`, o diretório `bin/` pode conter todos os recursos
necessários para executar o protocolo, incluindo:

- bibliotecas compartilhadas;
- certificados;
- chaves;
- arquivos de configuração;
- binários auxiliares;
- scripts auxiliares;
- tabelas ou dados utilizados pelo protocolo.

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

As dependências necessárias devem:

1. estar incorporadas aos executáveis; ou
2. estar disponíveis na imagem do container; ou
3. estar incluídas em `bin/` e ser localizadas pelos executáveis sem exigir
   instalação adicional.

Os executáveis são responsáveis por localizar corretamente seus arquivos
auxiliares. Eles não devem depender do diretório de trabalho utilizado para
iniciar o processo.

Recomenda-se localizar os recursos relativamente ao diretório do próprio
executável.

## Montagem nos containers

O testbed monta todo o conteúdo de `bin/` dentro dos containers.

O ponto de montagem é definido por `containers.binaries_directory` no arquivo
de cenário. Por padrão, ele corresponde a:

```text
/opt/protocol/bin
```

Assim, os pontos de entrada ficam disponíveis como:

```text
/opt/protocol/bin/sender
/opt/protocol/bin/receiver
```

O diretório é montado em modo somente leitura. Os binários não devem tentar:

- modificar seus próprios arquivos;
- modificar dependências ou configurações presentes em `bin/`;
- armazenar logs em `bin/`;
- armazenar resultados em `bin/`;
- criar arquivos temporários em `bin/`.

Caso o protocolo necessite escrever arquivos, deve usar um diretório gravável
do container, como `/tmp`, ou outro diretório fornecido pelo ambiente.

## Receiver

### Execução

O receiver é iniciado da seguinte forma:

```bash
./receiver --address <endereço> --port <porta>
```

### Argumentos

| Argumento | Descrição |
|---|---|
| `--address` | Endereço local no qual o receiver deve aceitar mensagens |
| `--port` | Porta local utilizada pelo protocolo |

O receiver deve validar os argumentos antes de iniciar sua operação.

### Estado pronto

O receiver deve:

1. validar seus argumentos;
2. inicializar a implementação do protocolo;
3. criar e configurar os recursos de comunicação necessários;
4. associar-se ao endereço e à porta recebidos;
5. ficar efetivamente disponível para receber mensagens;
6. somente então informar que está pronto.

Quando estiver pronto, o receiver deve escrever exatamente a seguinte linha em
`stdout`:

```text
READY
```

A linha deve terminar com uma quebra de linha e ser disponibilizada
imediatamente, sem permanecer retida em um buffer.

Por exemplo, em C:

```c
printf("READY\n");
fflush(stdout);
```

Em Python:

```python
print("READY", flush=True)
```

O receiver não deve emitir `READY` antes de concluir sua inicialização.

Se a inicialização falhar, ele deve:

- não emitir `READY`;
- registrar o erro em `stderr`, quando possível;
- encerrar com código diferente de zero.

Depois de emitir `READY`, o receiver deve permanecer em execução e disponível
para receber mensagens até ser encerrado pelo testbed.

## Sender

### Execução

O sender é iniciado da seguinte forma:

```bash
./sender \
  --mode <unicast|broadcast> \
  --destination <endereço> \
  --port <porta> \
  --count <quantidade>
```

### Argumentos

| Argumento | Descrição |
|---|---|
| `--mode` | Modo de transmissão: `unicast` ou `broadcast` |
| `--destination` | Endereço de destino da transmissão |
| `--port` | Porta de destino utilizada pelo protocolo |
| `--count` | Quantidade de transmissões solicitadas |

O sender deve:

1. validar seus argumentos;
2. inicializar a implementação do protocolo;
3. realizar a quantidade solicitada de transmissões;
4. encerrar depois de concluir sua execução.

Em modo `unicast`, `--destination` recebe o endereço do receiver selecionado
pelo cenário.

Em modo `broadcast`, `--destination` recebe o endereço de broadcast definido
pelo cenário ou calculado a partir da sub-rede.

## Configuração em tempo de execução

Os seguintes valores não devem estar fixados durante a compilação:

- endereço local do receiver;
- endereço de destino;
- porta;
- modo de transmissão;
- quantidade de transmissões.

Esses valores são definidos pelo cenário e fornecidos pelo testbed por meio dos
argumentos descritos neste contrato.

Outras configurações internas, como certificados, chaves, algoritmos e arquivos
específicos do protocolo, podem ser definidas pela implementação do usuário.

## Saída dos processos

Os binários podem escrever:

- informações operacionais em `stdout`;
- erros e informações de diagnóstico em `stderr`.

O testbed captura integralmente as duas saídas.

Não é exigido um formato específico para essas mensagens, com exceção da linha
`READY` emitida pelo receiver.

Os binários não precisam produzir:

- métricas de desempenho;
- timestamps;
- identificadores de sequência;
- contagens de pacotes;
- contagens de bytes;
- medições de latência;
- medições de perda;
- informações sobre retransmissões.

Essas medições são realizadas externamente pelo testbed.

## Encerramento

Os dois binários devem aceitar os sinais:

```text
SIGINT
SIGTERM
```

Ao receber um desses sinais, o processo deve:

1. interromper sua operação;
2. liberar sockets e demais recursos;
3. encerrar sem exigir interação do usuário.

Se o processo não encerrar dentro do limite definido no cenário, o testbed
poderá finalizá-lo com `SIGKILL`.

O sender normalmente encerra por conta própria depois de concluir as
transmissões.

O receiver normalmente permanece em execução até receber um sinal de
encerramento do testbed.

## Códigos de saída

Os binários devem utilizar os seguintes códigos de saída:

| Código | Significado |
|---:|---|
| `0` | Execução concluída ou encerramento controlado |
| `1` | Erro fatal durante a execução |
| `2` | Argumentos inválidos |

Outros códigos podem ser utilizados pela implementação, desde que sejam
documentados pelo usuário.

## Responsabilidades do testbed

Não fazem parte das responsabilidades dos binários:

- criar ou configurar a topologia;
- configurar interfaces de rede;
- inicializar o BATMAN-adv;
- controlar o `wmediumd`;
- iniciar capturas com `tcpdump`;
- calcular throughput;
- medir latência;
- estimar perda de pacotes;
- identificar retransmissões;
- coletar contadores das interfaces;
- coletar estatísticas do BATMAN-adv;
- organizar logs e arquivos PCAP;
- produzir os resultados do experimento.

Essas operações são realizadas pelo orquestrador e pelas ferramentas de
medição do testbed.