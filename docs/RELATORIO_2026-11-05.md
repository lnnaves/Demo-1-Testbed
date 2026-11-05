# Relatório do dia — 05/11/2026

**Resultado do dia:** a semana 2 do plano foi concluída e confirmada em
execução real. Cada execução agora gera uma pasta própria com:
- a cópia do config usado;
- o tempo de cada fase;
- as versões de tudo o que rodou;
- um PCAP por participante.

A primeira medida de latência foi feita à mão: **1,73 ms** do sender ao
receiver.

Todas as decisões do dia, com contexto, motivo e onde ficam no código, estão
em [`docs/DECISOES.md`](DECISOES.md) (D1 a D12). Este relatório resume o dia;
o `DECISOES.md` é a referência completa.

---

## 1. Retomada e revisão do projeto

- Releitura completa do repositório (código, testes, documentação, logs da
  prova de conceito) e do `docs/Plano_execucao`, que coincide com o plano do
  `CLAUDE.md`.
- Divergências encontradas:
  - o `RELATORIO_TECNICO.md` afirmava existir multi-hop no cenário padrão, o
    que é falso;
  - o validador ainda exige código de saída 0 dos receivers (pendência antiga).

## 2. Alcance do rádio: o testbed expõe, não garante (D7)

- **Problema:** o campo `range: 25` do config não tinha efeito. O alcance real
  é de ~60 m, então no cenário padrão (0/20/40 m) não há multi-hop.
- **Primeira proposta (recusada):** reposicionar os nós para forçar multi-hop.
- **Decisão:** o testbed **não garante** conectividade. Cada usuário escolhe
  as posições para descobrir a partir de que distância o protocolo dele deixa
  de funcionar bem, e o testbed registra o que aconteceu. Não se altera nem se
  calibra o rádio do Mininet-WiFi.
- **Feito:**
  - `range` removido (no Mininet-WiFi ele só gravava um número);
  - README com a seção "Alcance do rádio e posições";
  - tabela de sinal por distância medida na PoC: 15 m ≈ -53 a -74 dBm; 30 m ≈
    -68 a -82; 45 m ≈ -82 a -89; 60 m ≈ -90 (limiar -91).
- **Confirmado no host:** `success`, malha pronta em 10,0 s.

## 3. Espera da malha: origem, funcionamento e precisão (D3, D8)

Explicado e registrado:
- **Por que existe:** na semana 1 nenhum pacote era capturado, porque o
  protocolo começava antes de o BATMAN ter rotas.
- **Como funciona:** o comando `ping` do Linux, executado no **container** do
  sender (não o programa `bin/sender`), até cada receiver responder. O limite
  é de 60 s. As funções são `_ping` e `wait_for_network`, em
  `scripts/testbed/network.py`.
- **Por que não interfere no protocolo:** as fases são sequenciais, o cache
  ARP é limpo e a captura só começa depois.
- **Tempo limite × intervalo:** o limite (60 s) continua igual. O intervalo
  entre tentativas caiu de 1 s para 0,2 s.
  - **Correção:** a precisão real passa de ~2 s para **~1,2 s**, não para
    décimos, porque cada ping que falha ainda espera 1 s. Encurtar essa espera
    ficou como decisão em aberto.
- **Correção vista nos PCAPs:** o BATMAN tem um cache de ARP próprio (DAT) que
  a limpeza não apaga. O primeiro pacote do protocolo não paga a resolução
  ARP pela malha. O efeito é de frações de milissegundo.

## 4. Registro de decisões

- Criado o [`docs/DECISOES.md`](DECISOES.md), com a regra (no `CLAUDE.md`) de
  que toda decisão ou explicação nova vira uma entrada antes de seguir.

## 5. Semana 2, item 1: pasta por execução e validação estrita (D9)

- Cada execução grava em `logs/<experimento>/<AAAA-MM-DD_HH-MM-SS>/`, com
  nomes fixos. Duas execuções no mesmo segundo recebem o sufixo `-2`.
- A pasta base é escolhida ao rodar (`scripts/run.py --output-dir`, padrão
  `logs/`), não no config. O bloco `output:` foi removido.
- **Validação estrita:** campo desconhecido no config é erro. Isso pega erros
  de digitação como `postion:`, que antes rodavam com o valor padrão sem
  aviso. Substitui avisos de compatibilidade, que não fazem sentido enquanto
  não há usuários externos.
- **Confirmado no host:** 2 execuções geraram 2 pastas, e o config copiado é
  idêntico ao original.

## 6. Semana 2, item 2: tempos (D10)

- `timing.json` com início, fim, duração e status de cada uma das fases e do
  total. É gravado por último, inclusive quando a execução falha no meio.
- Instantes em segundos Unix (mesma base do PCAP) e em hora local. Durações
  pelo relógio monotônico.
- Processos com `started_at`/`ended_at` no `summary.json`.
- **Resultado real (28,4 s):**

| Fase | Tempo |
|---|---|
| `prepare_host` (limpeza prévia) | 3,4 s |
| `build_network` | 7,3 s |
| `wait_network` | 10,2 s |
| `execute_protocol` | 2,5 s |
| `cleanup` | 5,0 s |
| demais fases | ~0,03 s |

  - O protocolo ocupa ~9% do tempo.
  - O pacote do PCAP cai entre o início e o fim do sender, o que confirma a
    mesma base de tempo.
- **Os 2,5 s de `execute_protocol` explicados (D5):**
  - os programas de exemplo só enviam/recebem 1 pacote de 1 byte;
  - o tempo é quase todo espera fixa do testbed: 1 s de espera pelo receiver,
    1 s de **drain** e ~0,5 s para o receiver de exemplo parar.
- **Drain:** pausa após o sender terminar, para pacotes em trânsito chegarem
  antes de desligar os receivers. Sem ele, o testbed causaria "perdas"
  falsas.

## 7. Semana 2, item 3: metadados (D11)

- `metadata.json` com:
  - commit do testbed, e se há alterações não commitadas;
  - sha256 dos binários;
  - id da imagem Docker;
  - kernel e versão do BATMAN-adv;
  - versões dos pacotes;
  - comando e usuário;
  - aviso de que o fading do wmediumd não tem semente controlável (mesmo
    cenário ≠ resultados idênticos).
- Coleta isolada: se faltar uma ferramenta, o campo registra o erro e a
  execução segue.
- **Confirmado no host:** git lido mesmo com `sudo`, hashes conferidos,
  BATMAN-adv 2025.5, kernel 7.0.0-42.
- **Achado:** a imagem `drone:latest` era de antes da última mudança do
  Dockerfile. **A imagem foi refeita** (id novo `d7f035f1…`, confirmado no
  `metadata.json`).

## 8. Semana 2, item 4: PCAP por participante (D12)

- `capture-<nó>.pcap` para o sender e cada receiver. Por padrão, todo o
  tráfego da `bat0` é capturado. O filtro é opcional, em `capture.filter`.
- Um tcpdump que morre ao iniciar (ex.: filtro inválido) faz a execução falhar
  antes do protocolo.
- **Confirmado no host:**
  - o PCAP do sender tem ARP + UDP;
  - o do receiver tem o UDP chegando **1,73 ms** depois. É a primeira evidência
    direta de entrega.

## 9. Outras correções

- A ordem das mensagens com `| tee` foi corrigida (`flush`).
- O "nfsnobody" visto em `logs/` era efeito do sandbox do assistente. No host,
  os arquivos são do `root`.

---

## Números do dia

- **12 commits** na branch `semana1-execucao-real` (`52fca51` … `1df97aa`).
- **Testes:** de 157 para **187**, todos passando.
- **Módulos novos:**
  - `scripts/testbed/rundir.py`
  - `scripts/testbed/timing.py`
  - `scripts/testbed/metadata.py`
- **Testes novos:**
  - `test_rundir.py`
  - `test_timing.py`
  - `test_metadata.py`

## Estrutura de uma execução hoje

```text
logs/exp-01-unicast/2026-11-05_15-39-36/
    config.yml            cópia exata do config
    metadata.json         versões de tudo (D11)
    capture-drone1.pcap   PCAP do sender (D12)
    capture-gcs.pcap      PCAP do receiver (D12)
    metrics.csv           métricas do PCAP do sender
    summary.json          processos, captura, status
    timing.json           tempos por fase (D10)
```

## Estado do plano

- ✅ **Semana 1:** fazer funcionar
- ✅ **Semana 2:** saídas e tempo
- ⏭ **Semana 3:** latência. Casar pacotes entre o PCAP do sender e o de cada
  receiver (tshark, hash do payload), com latência por pacote, média, mediana,
  p95, máximo e entrega/perda. Testes com PCAPs sintéticos.
- Semanas 4–5: mobilidade
- Semana 6: contrato com marcadores e documentação

## Pendências fora do plano

1. Merge da branch `semana1-execucao-real` (semanas 1 e 2) na `main`.
2. O validador exige exit 0 dos receivers. Ajustar junto com a semana 6.
3. `BIN_INTERFACE.md` e `CONTEXTO_AGENTE.md` desatualizados (semana 6).
4. Em aberto (D8): encurtar a espera do ping para medir a malha em décimos.
5. `batman_orig_interval_ms` padrão para mobilidade (semanas 4–5).
6. O guia de integração (semana 6) deve dizer que a imagem precisa de `ping`.
