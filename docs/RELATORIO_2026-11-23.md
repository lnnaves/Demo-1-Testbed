# Relatório de 23/11/2026 — semanas 5 e 6

**Resultado:** as semanas 5 e 6 foram implementadas e confirmadas em
execução real no mesmo dia.
- **Semana 5:** o testbed gera sozinho as tabelas de entrega e latência por
  janela de tempo e por faixa de distância, cruzadas com a rota do BATMAN e
  o sinal de rádio.
- **Semana 6:** o usuário escreve no config o comando do seu programa, com
  marcadores, e o programa pode vir de uma imagem própria.

Duas pendências antigas também foram confirmadas: a análise nos dois
sentidos (desde a semana 3) e a cópia do CSV de trajetória (desde 09/11).

Decisões completas: [`DECISOES.md`](DECISOES.md), D16, D17 e D18.
Guia novo: [`GUIA_INTEGRACAO.md`](GUIA_INTEGRACAO.md).

---

## 1. Decisões do dia

| Decisão | Tema | Situação |
|---|---|---|
| D16 | Latência e perda por janela de tempo × posições (semana 5) | Aprovada, implementada, confirmada |
| D17 | Por que o testbed move os nós em vez de usar a mobilidade do Mininet-WiFi | Explicação registrada; modelos aleatórios ficam como tarefa extra, a decidir com o grupo |
| D18 | Contrato novo: comando com marcadores, pastas de dados e saída, status por tráfego IP (semana 6) | Aprovada, implementada, confirmada |

### D17 em poucas linhas

Pergunta: por que não usar `net.setMobilityModel(...)` do Mininet-WiFi?
- A mobilidade nativa começa na montagem da rede, não no início do
  protocolo: os nós já andariam durante a espera da malha (~10 s).
- Ela não grava as posições.
- Os modelos aleatórios são só 2D, com passo de 0,5 s.
- O laço de movimento ocupa um núcleo da CPU.

Para o usuário, o esforço é o mesmo: alguns campos no YAML. Trajetória
definida e modelo aleatório respondem a perguntas diferentes.

Caminho possível: usar os geradores de modelo do Mininet-WiFi dentro da
thread do testbed (estimativa de 1–2 dias). Fica para a reunião do grupo.

## 2. Semana 5 (D16)

**O que mudou:** `scripts/testbed/windows.py`, executado no fim de cada
execução, grava por sentido:
- `windows-<origem>-<destino>.csv`: uma linha por janela de tempo (padrão
  1 s), com distância, entregues e perdidos, latência, próximo salto, TQ e
  sinal;
- `distance-<origem>-<destino>.csv`: uma linha por faixa de distância
  (padrão 10 m).

Os tamanhos são configuráveis no bloco `analysis`. No terminal, sai uma
linha por sentido, por exemplo
`drone1->gcs: first loss at t=14.8s, at 84 m`. Janelas sem pacotes aparecem
com `sent = 0`.

**Conferência com os dados de 09/11:** reprocessando a exp-08 e a exp-09, as
tabelas batem com as feitas à mão. A diferença é de 1 pacote em algumas
faixas: pacotes enviados exatamente na borda, já que o testbed interpola a
posição.

### Execuções reais

| Execução | Cenário | Resultado |
|---|---|---|
| exp-11 (`poc_semana5_ping.yml`) | Relay a 75 m, trajetória em CSV, sender = `ping` | Pedidos 627/689, respostas 557/627. **Dois sentidos confirmados**, com conferência independente pelo número de sequência do ICMP. Cópia do CSV de trajetória com sha256 igual |
| exp-12 (`poc_semana5_broadcast.yml`) | Broadcast, drone1 de 20 a 150 m, receivers gcs (0 m) e drone2 (75 m), janela 2 s, faixas 5 m | **697/697 nos dois receivers.** O gcs passou a receber pelo drone2: mediana de 0,93 para 1,83 ms a ~111 m |
| exp-13 (`poc_semana5_unicast.yml`) | O mesmo cenário em unicast; drone2 só como relay | **627/697.** Perda de 85 a 120 m com a rota ainda direta; depois da troca para o drone2 (~120 m), ~100% e 1–3,5 ms |

### Achados

- **A troca de rota não é simétrica** (exp-11): o gcs voltou ao enlace
  direto por 1 s enquanto o drone1 já usava o relay, e as respostas
  perderam mais que os pedidos. Para protocolos de diálogo (EDHOC), basta
  um sentido falhar.
- **Broadcast × unicast no mesmo cenário:** o broadcast usa o relay
  automaticamente (o BATMAN inunda a malha); o unicast depende de o BATMAN
  trocar a rota. A diferença está toda no trecho antes da troca.
- **A variação entre execuções continua:** a primeira perda foi a 63 m na
  exp-11 e a 76 m na exp-09, no mesmo cenário.

## 3. Semana 6 (D18)

**Como o usuário passa a escrever o comando:**

```yaml
commands:
  sender:   "echo_client --server {destination} --port {port} --message-file {data_dir}/message.txt --log {output_dir}/client.log"
  receiver: "echo_server --bind {address} --port {port} --log {output_dir}/server.log"
```

**Marcadores:** `{address}`, `{destination}`, `{port}`, `{node_id}`,
`{mode}`, `{data_dir}` e `{output_dir}`. Marcador desconhecido é erro.

**Como o programa roda:**
- sem shell, para o SIGTERM chegar direto ao programa;
- com as variáveis `TESTBED_*` no ambiente;
- com `protocol-data/<nó>/` em `/opt/protocol/data`, só para leitura;
- na pasta de trabalho `/opt/protocol/out`, que é gravada em `out-<nó>/` na
  pasta da execução.

**Status:** `success` passa a valer com qualquer pacote IPv4 do sender para
um receiver, de qualquer protocolo ou porta.

**Outras mudanças:**
- O `validate_scenario.py` foi ajustado: aceita qualquer código de quem o
  testbed encerrou e compara o comando esperado.
- Sem `commands`, tudo funciona como antes.

**Exemplo completo:** [`examples/echo/`](../examples/echo/), um par de
pergunta e resposta em Python com imagem `FROM drone:latest`.

### Execuções reais

| Execução | Resultado |
|---|---|
| exp-14 (`examples/echo/config.yml`, imagem `drone-echo:latest`) | `success`, 50/50 nos dois sentidos. `client.log` com `role=sender ... cwd=/opt/protocol/out` e `answered 50/50`; o servidor gravou o log ao receber o SIGTERM. O sender terminou sozinho (`ended_by: self`, código 0) |
| exp-01 (`scripts/config.yml`, sem `commands`) | `success`, 199/199, comandos iguais aos de antes: compatibilidade confirmada |

**Observação em aberto:** no exemplo echo, a pergunta levou ~1,66 ms e a
resposta ~0,17 ms, com a mesma taxa do Wi-Fi nos dois sentidos. A
hipótese da taxa não explica este caso. Hipótese nova, não verificada:
custo de acesso ao meio depois de 0,1 s de canal ocioso.

### Documentação

- Novo: `docs/GUIA_INTEGRACAO.md`.
- Atualizados: README e `bin/BIN_INTERFACE.md`.
- `docs/CONTEXTO_AGENTE.md` recebeu um aviso de documento histórico, com a
  lista das regras revogadas.

## 4. Testes automáticos

TESTE AUTOMÁTICO: 243 testes (eram 219). Novos: `tests/test_windows.py`
(8) e `tests/test_contract.py` (10), mais testes de status, do validador e
de contrato.

Uma falha **anterior e alheia** ao trabalho do dia: o
`test_repository_contract` acusa `scripts/teste.yml`, um arquivo da usuária
que está no índice do Git. Falta decidir o que fazer com ele.

## 5. Commits (branch `semana1-execucao-real`, sem push)

`a51484e` (semana 5), `5bc456d` (broadcast), `ab79d36` (unicast), `ef45450`
(semana 6), `5983aa1` (compatibilidade), `6b143a0` (guia e documentação) e
o commit deste relatório.

Configs temporários fora do Git: `poc_semana5_ping.yml`,
`poc_semana5_broadcast.yml` e `poc_semana5_unicast.yml`, e o
`bin/poc_ping_sender`, que o Git ignora.

## 6. Pendências

- Atualizar o `TESTBED_GUIA_COMPLETO.md` (estado de 09/11).
- Repetições com média, desvio e IC 95% (o item "se der tempo" da semana 6).
- Modelos de mobilidade aleatórios (D17): decisão do grupo.
- `scripts/teste.yml`: decisão da usuária.
- Ainda não exercitados no real: TCP e um protocolo real (PQ-EDHOC).
- Escolher o `batman_orig_interval_ms` padrão para mobilidade.
- Push e PR das semanas 5 e 6 (só se a usuária pedir).
