# Prompt de retomada

Copie o bloco abaixo e cole como **primeira mensagem** numa conversa nova
neste projeto.

```text
Retomando o testbed (Demo-1-Testbed). Siga esta ordem, sem ler o repositório
inteiro, para economizar contexto.

1. CONTEXTO (já carregado automaticamente: CLAUDE.md). Ele é a fonte de
   verdade: objetivo, fronteira testbed × usuário, decisões de design,
   achados técnicos, ambiente, estado atual e plano com checkboxes.

2. LEIA AGORA, nesta ordem:
   a) docs/DECISOES.md — D1 a D13, com o porquê de cada decisão. Atenção às
      correções dentro da D3 (DAT do BATMAN) e da D8 (precisão ~1,2 s).
      A D13 (08/11) está EM DISCUSSÃO: casamento de pacotes por ID do IP.
   b) docs/RELATORIO_2026-11-05.md — o que foi feito no último dia.
   NÃO leia README.md, RELATORIO_TECNICO.md, CONTEXTO_AGENTE.md,
   BIN_INTERFACE.md, third-party/ nem os testes antigos, a menos que a
   tarefa exija. CONTEXTO_AGENTE.md e BIN_INTERFACE.md estão desatualizados
   (contrato antigo); onde conflitarem, CLAUDE.md vence.

3. ONDE PARAMOS: semanas 1 e 2 concluídas e confirmadas no host. Suíte com
   187 testes OK. Semanas 1 e 2 já estão na main (PRs #20 e #21). Não afirme
   o estado do GitHub sem "git fetch". Próximo: SEMANA 3, latência.
   Em 08/11 só houve discussão (D13): a usuária questionou o hash; a
   recomendação passou a ser casar pelo ID do cabeçalho IP. Retome tirando
   dúvidas restantes e pedindo o ok das decisões da D13 antes de codar.

4. PARA A SEMANA 3, leia só estes arquivos de código:
   - scripts/testbed/metrics.py (Capture, CaptureSet, analyze_pcap,
     write_outputs; o summary["capture"]["nodes"] tem um PCAP por
     participante)
   - scripts/testbed/rundir.py (output["pcaps"] = {nó: caminho})
   - scripts/run.py (fases; onde a análise nova entraria)
   - tests/test_repository_contract.py (proíbe "latency_" em metrics.py ->
     a latência vai num módulo novo, ex.: scripts/testbed/latency.py)

   Escopo da semana 3 (do plano):
   - casar o mesmo pacote no PCAP do sender e no de cada receiver pelo hash
     do payload, com tshark (pré-requisito novo no host; remonta fragmentos;
     só UDP);
   - saídas: latência por pacote (CSV), média, mediana, p95, máximo, taxa de
     entrega e perda; em broadcast, por receiver;
   - testes com PCAPs sintéticos, que não exigem rede real.

   Fatos que importam:
   - timestamps do PCAP e do testbed estão na mesma base (epoch);
   - relógio compartilhado entre containers;
   - primeira medida manual: 1,73 ms (D12);
   - payload duplicado (mesmo conteúdo enviado 2 vezes) precisa de regra de
     casamento;
   - drain_seconds precisa ser maior que a latência máxima, senão a "perda" é
     artefato do testbed;
   - o testbed nunca interpreta o payload: hash é só para casar pacotes;
   - "interval_*" não é latência; não renomear.

   Decisões a levar à usuária ANTES de codar (explique de forma simples):
   - os testes rodam no sandbox, que não tem tshark: gerar PCAPs sintéticos
     em Python puro e (a) testar o casamento sobre dados já extraídos e (b)
     pular os testes que chamam tshark quando ele não existir?
   - nome e formato dos arquivos novos na pasta da execução
     (ex.: latency-<receiver>.csv e um bloco "latency" no summary.json);
   - o que fazer com pacotes não-UDP ou de outras portas.

5. COMO TRABALHAR COM A USUÁRIA (importante):
   - Ela quer ENTENDER antes de avançar: explique de forma simples o que
     temos hoje, o que vai mudar, por quê, e de onde surgiu a necessidade.
     Use tabelas curtas e exemplos concretos. Não avance sem o "ok" dela em
     decisões de design.
   - Toda decisão ou explicação nova vira entrada em docs/DECISOES.md (D13,
     D14...) ANTES de seguir: contexto/quando surgiu, decisão, por quê, onde
     no código, implicações e, depois, a confirmação real.
   - Seja honesto: se algo prometido não se confirmar, registre como
     "Correção" na decisão correspondente.
   - Ciclo: implementar + testes -> UM comando para o host + o que se espera
     ver -> ela roda e cola a saída -> você confere os arquivos gerados ->
     marca o checkbox no CLAUDE.md -> commit.
   - Commits só na branch atual, mensagem em português, com a linha
     Co-Authored-By. Não fazer merge/push sem pedido.

6. AMBIENTE (o shell do assistente é um sandbox Flatpak):
   - não tem docker, mn, tcpdump nem tshark; a execução real é sempre no
     host, pela usuária, com: sudo python3 scripts/run.py 2>&1 | tee <log>
   - os testes rodam aqui com pyyaml no scratchpad:
       pip install --target <scratchpad>/pylib pyyaml
       PYTHONPATH=<scratchpad>/pylib python3 -m unittest discover -s tests
   - os resultados ficam legíveis aqui em logs/<exp>/<data-hora>/. Os PCAPs
     podem ser decodificados com Python (struct), já que não há tcpdump.
   - arquivos do root aparecem como "nfsnobody" aqui; é só o sandbox.
   - ao dar comandos para o host, não dependa da saída de "ls" (o terminal
     dela tem ls com cores, eza); use caminhos explícitos ou "command ls".

Confirme em poucas linhas que entendeu onde paramos e o que é a semana 3, e
então me apresente, de forma simples, as decisões do item 4 antes de
implementar.
```

## Variações

- Para começar pelo PR da semana 2:
  troque a última frase por "Antes da semana 3, confira se a semana 2 já
  está na main e, se não estiver, me explique como abrir o PR".
- Para só tirar dúvidas: troque a última frase por "Ainda não implemente
  nada; quero tirar dúvidas".
