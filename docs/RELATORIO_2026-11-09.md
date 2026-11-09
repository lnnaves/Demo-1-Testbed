# Relatório do dia — 09/11/2026

**Resultado do dia:** a semana 3 (latência) foi implementada e confirmada em
execução real para **latência e entrega**, em unicast e broadcast. A
**perda** ainda não foi vista no real: até 70 m nenhum pacote se perdeu. A
validação real da perda ficou para a mobilidade (semanas 4–5).

As decisões do dia, com contexto e motivo, estão em
[`docs/DECISOES.md`](DECISOES.md), na D13 (latência) e na D14 (testes
automáticos). Este relatório resume; o `DECISOES.md` é a referência.

---

## 1. Decisões sobre a latência (D13)

| Tema | Decisão |
|---|---|
| O que se mede | Latência **de rede** fim-a-fim (`bat0` → `bat0`, todos os saltos). Não de programa a programa: os meios práticos exigiriam interferir no protocolo ou interpretá-lo. Ver [`LATENCIA_REDE_X_PROGRAMA.md`](LATENCIA_REDE_X_PROGRAMA.md) |
| Como reconhecer o pacote | **ID do cabeçalho IP** + IPs + protocolo + portas + tamanho. O hash do payload falha com conteúdo repetido; só contar pacotes não dá latência |
| Ferramenta | Python puro lê o PCAP. O `tcpdump` continua capturando. O `tshark` não é dependência: fica como sugestão para o usuário visualizar o tráfego |
| Que tráfego | Todo pacote IP entre participantes, **nos dois sentidos** (o EDHOC é um diálogo), separado por fluxo. O "só UDP" do plano antigo era herança do hash e foi removido |
| Janela de casamento | 5 s. O ID do Linux sobe com saltos aleatórios e se repete a cada ~2.800 pacotes; sem a janela, uma perda poderia ser escondida por um casamento errado |
| Métricas | Enviados, entregues, perdidos, taxa de perda, maior rajada de perda (*loss burst*), fora de ordem, duplicados; latência mín/mediana/média/p95 (n ≥ 20)/p99 (n ≥ 100)/máx; jitter |
| Status | A análise nunca muda o status da execução: perder pacotes é resultado do cenário, não falha do testbed |

## 2. Testes automáticos (D14)

- Só a execução **REAL** no host valida. Testes automáticos (sandbox,
  mocks, PCAPs sintéticos) só avisam se algo que funcionava quebrou. Todo
  relato separa REAL de TESTE AUTOMÁTICO.
- A pasta `tests/` será removida no fim do projeto. Verificado: nada do
  testbed depende dela; vira regra.

## 3. Implementação

- `bin/sender` (exemplo): 20 pacotes iguais (`\x00`), 50 ms entre eles.
  Payload igual de propósito: é o caso que o hash não resolveria.
- `scripts/testbed/pcap.py`: leitor de PCAP; agrupa fragmentos pelo ID.
- `scripts/testbed/latency.py`: casamento e métricas.
- Saídas novas na pasta da execução: `latency-<origem>-<destino>.csv`, um
  pacote por linha, e o bloco `latency` no `summary.json`. Fase nova
  `analyze_network` no `timing.json` (~1 ms). Resumo por sentido no
  terminal.
- TESTE AUTOMÁTICO: 202 OK (15 novos).
- Commits `80e2d02` e `a231129` na branch `semana1-execucao-real` (sem push).

## 4. Execuções reais

| Execução | Distância | Entregues | Mediana | Máx | Jitter |
|---|---|---|---|---|---|
| Etapa 0: IDs nos PCAPs | 20 m | 20/20 (à mão) | 0,25 ms | 1,18 ms | — |
| Unicast | 20 m | 20/20 | 0,240 ms | 1,166 ms | 0,157 ms |
| Broadcast → gcs / drone2 | 20 / 40 m | 20/20 cada | 0,929 / 0,986 ms | 0,978 / 1,029 ms | 0,040 / 0,045 ms |
| Unicast | 50 m | 20/20 | 0,922 ms | 1,147 ms | 0,226 ms |
| Broadcast | 50 m | 20/20 | 0,938 ms | 4,191 ms | 0,549 ms |
| Broadcast | 58 m | 20/20 | 0,937 ms | 1,613 ms | 0,108 ms |
| Broadcast | 62 m | 20/20 | 0,924 ms | 3,639 ms | 0,399 ms |
| Broadcast | 70 m | 20/20 | 0,940 ms | 3,198 ms | 0,277 ms |

- Em todas as execuções a conferência independente (decodificar os PCAPs
  por outro caminho) bateu com o testbed.
- O ID do IP nunca foi zero nem se repetiu, e foi igual nos PCAPs do sender
  e dos receivers.
- Com a distância, o enlace piora como **atraso**, não como perda: a
  mediana fica igual, o máximo e o jitter sobem.

## 5. Correções e hipóteses

- **Correção:** o ID do IP não sobe de 1 em 1 (saltos de ~23 em média).
- **Correção:** o "alcance ~60 m" anotado antes estava errado. Até 70 m,
  com o drone parado, tudo chegou. O "-90 dBm" do log da PoC era o último
  valor recebido, não o sinal real.
- **Hipóteses, não verificadas** (em
  [`HIPOTESES_2026-11-09.md`](HIPOTESES_2026-11-09.md)): o broadcast é mais
  lento e mais estável porque sai na taxa básica e sem reenvio; com sinal
  fraco, o Wi-Fi (unicast) e o BATMAN (broadcast) reenviam, e a perda vira
  atraso. Verificação proposta, só observando: `iw dev wlan0 station dump`
  e captura na `wlan0`.

## 6. Pendências

- Perda real: validar na mobilidade (semanas 4–5).
- Arquivos temporários `poc_*.yml` na raiz (não versionados): apagar quando
  não forem mais úteis.
- `metrics.csv` (intervalos do sender, só UDP na porta do protocolo)
  continua existindo ao lado da análise nova; decidir se ainda faz sentido.
- Janela de 5 s fixa no código; configurável só se a usuária pedir.
