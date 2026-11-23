# Como integrar o seu protocolo ao testbed

Guia para quem quer rodar um protocolo próprio (EDHOC, PQ-EDHOC, TESLA,
telemetria...) nos drones emulados. Estado em 23/11/2026, com o contrato da
semana 6 (decisão D18 em [`DECISOES.md`](DECISOES.md)), confirmado em
execução real com o exemplo [`examples/echo/`](../examples/echo/).

Você não precisa alterar o seu programa para o testbed: basta que ele rode
em Linux, sem interação, e aceite endereço e porta pela linha de comando,
em qualquer formato. O testbed nunca lê o conteúdo das mensagens nem a
saída do programa.

---

## 1. Quem faz o quê

| O testbed cuida de | Você cuida de |
|---|---|
| Rádio emulado, roteamento BATMAN, IPs, posições e movimento | O programa do protocolo |
| Esperar a rede ficar pronta antes de iniciar o seu programa | Bibliotecas, chaves, certificados |
| Iniciar e encerrar os programas | Uma imagem Docker `FROM drone:latest` com tudo isso instalado |
| Gravar o tráfego (PCAP), medir entrega, perda e latência | Dizer no config como o programa é chamado |
| Guardar stdout, stderr e os arquivos que o programa gravar | |

O testbed **não instala** dependências do protocolo.

---

## 2. O que o seu programa precisa ter

1. **Rodar em primeiro plano, sem interação** (sem pedir senha, sem menu).
2. **Aceitar o endereço e a porta pela linha de comando**, no formato que
   ele já usa (ex.: `--host 1.2.3.4`, `coap://1.2.3.4:5683`).
3. **Usar IPv4.** A análise de latência e perda lê só IPv4. Algumas
   bibliotecas (CoAP, por exemplo) usam IPv6 por padrão: force IPv4.
4. **Encerrar quando receber SIGTERM**, se o programa não terminar sozinho.
   Ser encerrado pelo testbed é normal: fica registrado como
   `ended_by: testbed`, e qualquer código de saída é aceito.
5. Se o programa **termina sozinho** (ex.: um handshake), ele deve sair com
   código 0. Outro código vira falha (`sender exited with code N`).

Dois papéis existem em toda execução:
- **sender:** o nó que inicia a comunicação (ex.: initiator do EDHOC). Há
  sempre um.
- **receiver:** os nós que esperam (ex.: responder). Um em unicast; um ou
  mais em broadcast. Hoje todos os receivers usam o mesmo comando.

O receiver é iniciado primeiro; o sender começa 1 s depois
(`experiment.receiver_startup_seconds`).

---

## 3. Passo a passo

### Passo 1: a imagem Docker

Crie um `Dockerfile` que parte da imagem dos drones e instala o seu
programa e as dependências dele:

```dockerfile
FROM drone:latest
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY meu-protocolo /usr/local/bin/meu-protocolo
```

```bash
bash scripts/build-docker.sh                # imagem base drone:latest (uma vez)
docker build -t meu-protocolo:latest caminho/da/pasta
```

Mantenha o `FROM drone:latest`: a imagem base tem o `ping` (usado para
saber quando a rede está pronta) e o `/bin/sh` (usado para iniciar o seu
programa na pasta de saída).

Alternativa sem imagem própria: um executável que não precisa de nada além
da imagem base pode ficar em `bin/` do repositório; ele aparece no container
em `/opt/protocol/bin/`.

### Passo 2: dados por nó (opcional)

Chaves, certificados ou arquivos de configuração de cada nó ficam em
`protocol-data/<nome do nó>/` na raiz do repositório:

```
protocol-data/
  drone1/  initiator.key  cert.pem
  gcs/     responder.key  cert.pem
```

Dentro do container, cada nó vê **só a sua pasta**, em `/opt/protocol/data`
(só leitura). Nó sem pasta: nada é montado.

### Passo 3: o config

Copie `examples/echo/config.yml` (ou `scripts/config.yml`) e mude:
- `nodes[].image`: a sua imagem (ex.: `meu-protocolo:latest`);
- `protocol`: `mode` (`unicast` ou `broadcast`), `port`, `sender`,
  `receivers`;
- `experiment.end`: `sender` se o seu programa termina sozinho (o
  `duration_seconds` vira só um limite de segurança); `duration` se ele
  roda até ser encerrado (o testbed para o sender em `duration_seconds`);
- o bloco **`commands`**, com o comando de cada papel:

```yaml
commands:
  sender:   "edhoc-initiator --peer coap://{destination}:{port} --key {data_dir}/initiator.key --log {output_dir}/edhoc.log"
  receiver: "edhoc-responder --bind {address} --port {port} --key {data_dir}/responder.key"
```

O testbed troca os marcadores pelos valores de cada nó:

| Marcador | Valor | Exemplo |
|---|---|---|
| `{address}` | IP do próprio nó | `192.168.123.1` |
| `{destination}` | Unicast: IP do receiver. Broadcast: IP de broadcast | `192.168.123.1` / `192.168.123.255` |
| `{port}` | `protocol.port` | `5000` |
| `{node_id}` | Nome do nó | `drone1` |
| `{mode}` | `unicast` ou `broadcast` | `unicast` |
| `{data_dir}` | Pasta de dados do nó | `/opt/protocol/data` |
| `{output_dir}` | Pasta de saída do nó | `/opt/protocol/out` |

Regras do comando:
- **Não há shell.** O comando é dividido em argumentos como num terminal
  (aspas funcionam: `--name 'a b'`), mas `|`, `>`, `&&` e `$VAR` não são
  interpretados. Isso garante que o SIGTERM chegue direto ao seu programa.
- Precisa de shell? Use `sh -c 'exec meu-programa ...'`. O `exec` é
  importante: sem ele, o SIGTERM vai para o shell e o seu programa pode
  não ser encerrado.
- Chave literal: `{{` e `}}` (ex.: um JSON no argumento).
- Marcador escrito errado (ex.: `{adress}`) é erro antes de rodar, com a
  lista dos marcadores válidos.
- Sem `commands`, valem os comandos dos binários de exemplo
  (`--destination/--port` e `--address/--port`).

### Passo 4: rodar

```bash
sudo python3 scripts/run.py meu-config.yml 2>&1 | tee minha-execucao.log
```

Na tela:

```
Run directory: .../logs/<experimento>/<data-hora>
Mesh ready after 8.1s
drone1->gcs: 50/50 delivered, median latency 1.657 ms
gcs->drone1: 50/50 delivered, median latency 0.165 ms
drone1->gcs: no loss
gcs->drone1: no loss
Experiment completed successfully.
```

### Passo 5: ler os resultados

Tudo fica em `logs/<experiment.id>/<data-hora>/`:

| Arquivo | O que tem |
|---|---|
| `out-<nó>/` | Tudo o que o seu programa gravou em `/opt/protocol/out` |
| `summary.json` | Status, comando exato de cada processo, stdout, stderr, código de saída, `ended_by`, totais de latência e perda |
| `capture-<nó>.pcap` | Todo o tráfego de cada participante (abre no Wireshark) |
| `latency-<origem>-<destino>.csv` | Um pacote por linha: entregue ou perdido, latência |
| `windows-` e `distance-<origem>-<destino>.csv` | Entrega e latência por janela de tempo e por faixa de distância, com rota e sinal |
| `positions.csv`, `routes.csv`, `signal.csv` | Posições, rotas do BATMAN e sinal de rádio ao longo do tempo |
| `config.yml`, `metadata.json`, `timing.json` | Config usado, versões (inclusive o id da sua imagem), tempo de cada fase |

---

## 4. O que o seu programa encontra dentro do container

| Item | Valor |
|---|---|
| Pasta de trabalho inicial | `/opt/protocol/out` (gravada em `out-<nó>/` na pasta da execução) |
| Dados do nó (só leitura) | `/opt/protocol/data` (de `protocol-data/<nó>/`), se existir |
| Executáveis de `bin/` | `/opt/protocol/bin/` (só leitura) |
| Interface de rede do protocolo | `bat0`, com o IP de `{address}` |
| Variáveis de ambiente | `TESTBED_NODE_ID`, `TESTBED_ROLE` (`sender`/`receiver`), `TESTBED_MODE`, `TESTBED_DATA_DIR`, `TESTBED_OUTPUT_DIR` |
| Usuário | root |

O programa não precisa usar as variáveis; elas existem para quem prefere
ler configuração do ambiente em vez de argumentos.

---

## 5. O status da execução

- **`success`:** os programas iniciaram e terminaram como esperado, a
  captura é válida e o PCAP do sender tem pelo menos um pacote IP do sender
  para um receiver (qualquer protocolo ou porta: UDP, TCP, ICMP...).
- **`inconclusive`:** tudo rodou, mas nenhum pacote do sender para um
  receiver apareceu (ex.: o programa usou IPv6, outra interface, ou não
  enviou nada).
- **`failed`:** um programa falhou (saiu sozinho com código diferente de 0,
  não iniciou, ou o receiver morreu antes da hora) ou a captura falhou.

**Perder pacotes nunca é falha.** É resultado do cenário, e aparece nas
medidas.

---

## 6. O que é medido e o que não é

- **Latência de rede**, da `bat0` do nó que envia à `bat0` do nó que
  recebe, com todos os saltos. **Não** é a latência de programa a programa:
  o tempo que o seu programa gasta (ex.: cifrar e decifrar) não entra. Se
  você precisa dele, meça dentro do programa e grave em `{output_dir}`.
- **Entrega e perda** por pacote IP, nos dois sentidos, por fluxo
  (protocolo + portas). Os pacotes são reconhecidos pelo ID do cabeçalho
  IP, posto pelo kernel; o conteúdo nunca é lido. Em TCP, um reenvio conta
  como outro pacote.
- Um pacote que chega à `bat0` do receiver mas que o programa não leu
  conta como entregue.
- IPv6 é capturado, mas não entra na análise.

---

## 7. Exemplo completo: `examples/echo/`

Um protocolo mínimo de pergunta e resposta, em Python, integrado só pelo
contrato acima:

| Arquivo | Papel |
|---|---|
| `Dockerfile` | `FROM drone:latest` + copia os dois programas para `/usr/local/bin` |
| `echo_client.py` | Sender: lê a mensagem de `{data_dir}/message.txt`, faz 50 perguntas e espera cada resposta, grava `client.log` em `{output_dir}`, termina sozinho |
| `echo_server.py` | Receiver: responde cada pergunta até o SIGTERM, grava `server.log` |
| `config.yml` | `end: sender`, imagem `drone-echo:latest`, bloco `commands` |
| `protocol-data/drone1/message.txt` | Dado do nó drone1 |

```bash
docker build -t drone-echo:latest examples/echo
sudo python3 scripts/run.py examples/echo/config.yml
```

Resultado real (23/11): `success`, 50/50 nos dois sentidos,
`out-drone1/client.log` com `role=sender ... cwd=/opt/protocol/out` e
`answered 50/50`.

---

## 8. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `configuration error: commands.sender: unknown placeholder {adress}` | Marcador digitado errado |
| `network not ready: drone1 could not reach gcs` | Nós longe demais no início, ou imagem sem `ping`. Os nós precisam se alcançar na posição inicial |
| Erro de imagem não encontrada | Faltou o `docker build` da sua imagem |
| `sender exited with code 127` e `not found` no `stderr` | O programa não está no caminho indicado dentro da imagem (ou a imagem não é a esperada) |
| `sender exited with code N` | O programa terminou sozinho com erro: veja `stderr` no `summary.json` e os arquivos em `out-<nó>/` |
| `sender timed out` | Com `end: sender`, o programa passou de `duration_seconds`: aumente o limite ou use `end: duration` |
| `receiver ... exited unexpectedly` | O receiver terminou antes de o testbed encerrá-lo: ele precisa continuar rodando até o SIGTERM |
| `inconclusive` | Nenhum pacote IPv4 do sender para um receiver: confira IPv4, endereço (`{destination}`) e porta |
| Programa não encerra, execução demora | Ele ignora SIGTERM (ou está atrás de um `sh -c` sem `exec`); o testbed espera `shutdown_timeout_seconds` e força o fim |

---

## 9. Limites atuais

- Mesmo comando para todos os receivers.
- Um único sender por execução.
- Os processos rodam fora do limite de memória do container.
- O rádio emulado tem aleatoriedade sem semente: o mesmo config não dá
  exatamente os mesmos números. Para comparar, repita a execução.

Mais detalhes: [`TESTBED_GUIA_COMPLETO.md`](TESTBED_GUIA_COMPLETO.md) e
[`DECISOES.md`](DECISOES.md) (D13: latência; D15: mobilidade; D16:
janelas; D18: este contrato).
