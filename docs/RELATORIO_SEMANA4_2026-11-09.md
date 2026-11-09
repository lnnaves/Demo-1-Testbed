# Relatório da semana 4 (mobilidade) — 09/11/2026, tarde

**Resultado:** a semana 4 foi implementada e confirmada em execução real no
mesmo dia. Os nós agora podem se mover durante o experimento, o testbed
registra posições, rotas do BATMAN e sinal de rádio, e o experimento pode
terminar por duração. Com isso, **a perda de pacotes apareceu pela primeira
vez numa execução real**: 407 de 698 pacotes entregues, com a perda começando
quando o drone passou de 84 m.

Decisões completas: [`DECISOES.md`](DECISOES.md), D15. A semana 3 (latência)
está no [relatório da manhã](RELATORIO_2026-11-09.md).

---

## 1. Por que a semana 4 ficou mais importante

Na semana 3, nenhuma posição fixa (20 m a 70 m) gerou perda: a piora do
enlace aparecia só como atraso. Faltava validar no real a parte de perda. A
mobilidade resolve isso de forma natural: o drone começa perto (a malha
forma) e se afasta durante a execução.

## 2. Decisões aprovadas (D15)

| # | Tema | Decisão |
|---|---|---|
| 1 | Config | `nodes[].mobility`: `static` (padrão) ou `trajectory`, com pontos `[t, x, y, z]` no YAML ou num CSV (`waypoints_csv`); linha reta entre pontos; parado após o último |
| 2 | Quem move | Uma *thread* do testbed chama `setPosition()` a cada 0,1 s |
| 3 | Quando começa | t=0 é o início do sender; nada se move durante a espera da malha |
| 4 | Fim do experimento | Campo novo `experiment.end`: `sender` (como antes) ou `duration` (o testbed encerra o sender em `duration_seconds`; fim normal) |
| 5 | Sender de exemplo | Um pacote a cada 50 ms até receber SIGTERM |
| 6 | Logs | `positions.csv` (0,1 s), `routes.csv` e `signal.csv` (1 s) |
| 7 | Intervalo do BATMAN | Padrão continua 1000 ms; exemplos de mobilidade usam 200 ms |
| 8 | Validação | drone1 de 20 m a 150 m do gcs em 30 s |

## 3. O que mudou no código

| Arquivo | Mudança |
|---|---|
| `scripts/testbed/config.py` | `experiment.end` e `nodes[].mobility`, com validação (≥ 2 pontos; 1º em t=0 e igual a `position`; tempos crescentes; campos desconhecidos = erro) |
| `scripts/testbed/mobility.py` (novo) | Move os nós e grava `positions.csv` |
| `scripts/testbed/observer.py` (novo) | Lê `batctl o` e `iw station dump` de todos os nós a cada 1 s; grava `routes.csv` e `signal.csv`. Só leitura: nenhum pacote é enviado |
| `scripts/testbed/runner.py` | `duration_seconds` passa a contar do início do sender; `end: duration` torna o corte normal; campo `ended_by` (`self` / `testbed`) |
| `scripts/run.py` | Inicia e para movimento e observação junto com o protocolo; imprime resumo |
| `bin/sender` | Fluxo contínuo; erro de envio é contado e o envio continua |
| `scripts/config.yml` | `end: duration`, `duration_seconds: 10`; exemplo de `mobility` comentado |

**Acréscimos além do combinado (explicados à usuária):**
- o sender de exemplo não para mais quando um envio falha (sem rota, fora
  de alcance): conta o erro e continua; senão a perda viraria "sender
  falhou";
- `duration_seconds` conta do início do sender, não mais do início dos
  receivers.

TESTE AUTOMÁTICO: 217 OK (13 novos, com nós falsos).

## 4. Execuções reais

### 4.1 Mobilidade (`poc_mobilidade.yml`)

drone1 de 20 m a 150 m do gcs em 30 s (~4,3 m/s), parado em 150 m até 35 s;
drone2 a 200 m (sem relay); unicast; `orig_interval` 200 ms.

- `success`, sem avisos; total 62,6 s; 365 atualizações de posição; 39
  amostras de rotas/sinal.
- Sender: 698 enviados, 0 erros de envio, encerrado pelo testbed em 35,07 s.
- **407/698 entregues (41,7% de perda); maior rajada: 76 pacotes.**
  Conferência independente: 407.

| Distância do gcs | Entregues | Mediana | Máximo |
|---|---|---|---|
| 20–70 m | 229/229 | 0,23 ms | 4,4 ms |
| 70–80 m | 46/46 | 2,4 ms | 3,8 ms |
| 80–90 m | 37/46 | 2,7 ms | 111,6 ms |
| 90–100 m | 39/45 | 2,5 ms | 106,4 ms |
| 100–110 m | 25/46 | 60,6 ms | 111,5 ms |
| 110–120 m | 13/46 | 102,6 ms | 111,1 ms |
| 120–150 m | 14/137 | ~106 ms | 110,9 ms |
| 150 m (parado) | 2/101 | 107,7 ms | 110,1 ms |

Leitura:
1. A latência sobe antes da perda (×10 a partir de ~70 m).
2. O primeiro pacote perdido saiu com o drone a 84 m.
3. Não há corte abrupto: a perda cresce até quase 100%, e ainda chegam
   pacotes isolados a 150 m.

Observação (`signal.csv` e `routes.csv`):
- sinal -63 dBm a 20 m, -80 a 50 m, -88 a ~72 m, depois entre -83 e -90
  (é o sinal do último quadro recebido; não desce abaixo do limiar);
- `tx failed` do Wi-Fi começa a subir a ~111 m (1 → 157);
- `tx retries` ficou 0 o tempo todo: não confirma nem descarta reenvios;
- rota direta via gcs o tempo todo; TQ de 255 caiu a 133.

### 4.2 Config padrão novo (`scripts/config.yml`)

`end: duration`, 10 s: `success`, 199 enviados em 10,02 s, encerrado pelo
testbed, 199/199 entregues, p99 3,2 ms (passa a existir com n ≥ 100), total
39,0 s.

## 5. Hipóteses e achados novos

- **Taxa do Wi-Fi (hipótese):** mediana ~0,9 ms quando o Wi-Fi usou 7,2
  Mbit/s (config padrão), ~0,23 ms com 13 Mbit/s (mobilidade a 20–70 m). Pode
  explicar por que o broadcast (sempre na taxa básica) fica em ~0,93 ms.
  Detalhes em [`HIPOTESES_2026-11-09.md`](HIPOTESES_2026-11-09.md).
- **Convergência do BATMAN:** a espera da malha termina quando o ping passa,
  mas o TQ ainda sobe durante toda a execução (29 → 133 no config padrão).
  Sem efeito visível em enlace direto; pode importar em multi-hop.
- **Alcance (correção):** com o drone em movimento, a entrega foi 100% até
  ~84 m.

## 6. Commits (branch `semana1-execucao-real`, sem push)

`08dab2c` (semana 4), `0f9f9a0` (config padrão confirmado e hipótese da
taxa) e o commit destes documentos.

## 7. Pendências

- **Semana 5:** o testbed gerar sozinho a tabela da seção 4.1 (latência e
  perda por janela de tempo × posição).
- `end: sender` com timeout real ainda não foi exercitado no host (só em
  TESTE AUTOMÁTICO).
- Os `poc_*.yml` da manhã não têm `end`; se rodados de novo com o sender
  contínuo, terminam em "sender timed out" após 60 s. Continuam válidos como
  registro.
- Push e PR das semanas 3 e 4.
