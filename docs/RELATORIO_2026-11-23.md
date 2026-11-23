# Relatório de 23/11/2026 — semanas 5 e 6, Random Waypoint e repetições

**Resultado:** as semanas 5 e 6 foram implementadas e confirmadas em
execução real no mesmo dia.
- **Semana 5:** o testbed gera sozinho as tabelas de entrega e latência por
  janela de tempo e por faixa de distância, cruzadas com a rota do BATMAN e
  o sinal de rádio.
- **Semana 6:** o usuário escreve no config o comando do seu programa, com
  marcadores, e o programa pode vir de uma imagem própria.

Depois, no mesmo dia:
- **Random Waypoint (D19):** mobilidade aleatória própria, sorteada com
  seed e gerada como trajetória.
- **Repetições com estatística (D20):** `run.py --repeat N`, com média,
  desvio e IC 95%. Era o item "se der tempo" da semana 6.

Com isso, **todo o plano de 6 semanas está concluído**.

Duas pendências antigas também foram confirmadas: a análise nos dois
sentidos (desde a semana 3) e a cópia do CSV de trajetória (desde 09/11).

Decisões completas: [`DECISOES.md`](DECISOES.md), D16 a D20.
Guia novo: [`GUIA_INTEGRACAO.md`](GUIA_INTEGRACAO.md).

---

## 1. Decisões do dia

| Decisão | Tema | Situação |
|---|---|---|
| D16 | Latência e perda por janela de tempo × posições (semana 5) | Aprovada, implementada, confirmada |
| D17 | Por que o testbed move os nós em vez de usar a mobilidade do Mininet-WiFi | Explicação registrada; modelos aleatórios ficam como tarefa extra, a decidir com o grupo |
| D18 | Contrato novo: comando com marcadores, pastas de dados e saída, status por tráfego IP (semana 6) | Aprovada, implementada, confirmada |
| D19 | Random Waypoint próprio, gerado como trajetória (mantendo a trajetória definida) | Aprovada, implementada, confirmada |
| D20 | Repetições com estatística (`--repeat N`) | Aprovada, implementada, confirmada (com uma correção) |

### D17 em poucas linhas

Pergunta: por que não usar `net.setMobilityModel(...)` do Mininet-WiFi?
- A mobilidade nativa começa na montagem da rede, não no início do
  protocolo: os nós já andariam durante a espera da malha (~10 s).
- Ela não grava as posições.
- Os modelos aleatórios são só 2D, com passo de 0,5 s.
- O laço de movimento ocupa um núcleo da CPU.

Para o usuário, o esforço é o mesmo: alguns campos no YAML. Trajetória
definida e modelo aleatório respondem a perguntas diferentes.

Depois da explicação, a usuária decidiu manter a trajetória definida e
acrescentar um Random Waypoint próprio (D19, seção 4).

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

**Explicação registrada (D18): binário compilado no host basta?** Nem
sempre. Por padrão o binário é dinâmico e procura as bibliotecas onde
roda; o container (Ubuntu 24.04) não enxerga as do host, e um binário
compilado num Linux mais novo pode pedir uma glibc que o container não tem.
Decidido: imagem própria continua sendo o caminho recomendado.

## 4. Random Waypoint (D19)

**Escolha:** caminho B. O testbed sorteia, com `seed`, destinos uniformes
numa área 3D, velocidades (m/s, mínimo > 0) e pausas, e transforma isso
numa trajetória comum que começa na `position` em t = 0. O caminho A (usar
os geradores do Mininet-WiFi) foi descartado porque sorteia a posição
inicial (conflita com a espera da malha), é 2D, avança em passos de 0,5 s e
só seria testável no host.

```yaml
mobility:
  type: random_waypoint
  area: [[0, 0, 10], [120, 120, 30]]
  speed: [3, 8]
  pause: [0, 2]
  seed: 20          # opcional; sem ela, uma é sorteada e registrada
  duration: 60
```

O caminho sorteado vai para `trajectory-<nó>.csv` (reusável como
trajetória definida), e a seed e os parâmetros para o `metadata.json`.
Validação: testes da definição, teste estatístico (qui-quadrado dos
destinos, quartis das velocidades) e execução real.

**REAL, exp-15 (`poc_random_waypoint.yml`):** `trajectory-drone1.csv`
idêntico ao sorteado no sandbox com a seed 20; `positions.csv` segue o
caminho com desvio máximo de 0,004 m. 868/1194 entregues. Primeira perda a
84 m (de novo). A rota passou a ser via drone2 a 133 m; perdeu força com o
drone1 longe também do relay; **na volta**, a entrega se recuperou por
volta de 37 s pelo relay, e a rota voltou a ser direta a 42 s (~76 m). Foi
o primeiro teste em que um drone se afasta e volta.

## 5. Repetições com estatística (D20)

`sudo python3 scripts/run.py config.yml --repeat N [--vary-seed]` roda o
mesmo config N vezes em `logs/<exp>/<data-hora>-campanha/rep-NN/` e grava
`statistics.json`/`statistics.csv` com média, desvio e IC 95% (t de
Student). As medidas são: entrega, latência mín/mediana/média/p95/máx,
jitter, rajada, primeira perda (t e distância) e espera da malha. Uma
repetição com falha fica fora da estatística, com o motivo registrado.

**Correção:** na primeira tentativa real, a campanha parou depois da rep-01
com `KeyError: 'summary'`. Uma variável de laço tinha o mesmo nome do
dicionário devolvido por `run_once`. Corrigido, com teste de regressão.

**REAL, campanha de 5 do config padrão:** 5/5 usadas; mediana 0,239 ±
0,001 ms (IC 95% 0,238–0,241); p95 0,551 ± 0,183 ms; espera da malha 9,29
± 1,09 s. Conferido à mão a partir dos `summary.json`.

## 6. Testes automáticos

TESTE AUTOMÁTICO: **267 OK** (eram 219 no início do dia).

| Arquivo novo | Testes | O que cobre |
|---|---|---|
| `tests/test_windows.py` | 8 | Janelas e faixas (D16) |
| `tests/test_contract.py` | 10 | Contrato com marcadores (D18) |
| `tests/test_random_waypoint.py` | 12 | Random Waypoint, inclusive teste estatístico (D19) |
| `tests/test_campaign.py` | 11 | Estatística e laço de `--repeat` (D20) |

Também foram acrescentados testes de status, do validador, de contrato e
de regressão do `run_once`. O `scripts/teste.yml` (formato antigo, que
fazia o `test_repository_contract` falhar) foi removido a pedido da
usuária.

## 7. Commits (branch `semana1-execucao-real`, sem push)

`a51484e` (semana 5), `5bc456d` (broadcast), `ab79d36` (unicast), `ef45450`
(semana 6), `5983aa1` (compatibilidade), `6b143a0` (guia e documentação),
`aea24c6` (relatório e prompt), `0b77364` (guia completo), `6caeb40` (D19,
remoção do teste.yml), `62ba935` (Random Waypoint), `2671635` e `1076aac`
(repetições), e o commit de fechamento do dia.

Configs temporários fora do Git: `poc_semana5_ping.yml`,
`poc_semana5_broadcast.yml`, `poc_semana5_unicast.yml` e
`poc_random_waypoint.yml`, e o `bin/poc_ping_sender`, que o Git ignora.
Documentos de estudo da usuária também ficam fora do Git.

## 8. Pendências

- **PQ-EDHOC:** integrar pelos binários da usuária (próximo passo).
- **Push e PR** das semanas 5 e 6, do Random Waypoint e das repetições
  (a usuária faz ao encerrar o dia).
- No fim do projeto: resumo no README da diferença entre o Random Waypoint
  nativo e o do testbed.
- Ainda não exercitados no real: TCP e protocolo real.
- Escolher o `batman_orig_interval_ms` padrão para mobilidade.
- Observação em aberto: pergunta × resposta no exemplo echo.
- O `validate_scenario.py` foi ajustado, mas não foi rodado no host.
