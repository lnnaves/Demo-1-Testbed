# Prompt de retomada

Copie o bloco abaixo e cole como **primeira mensagem** numa conversa nova
neste projeto.

```text
Retomando o testbed (Demo-1-Testbed). Siga esta ordem, sem ler o repositório
inteiro, para economizar contexto.

1. CONTEXTO (já carregado automaticamente: CLAUDE.md). Ele é a fonte de
   verdade: objetivo, fronteira testbed × usuário, decisões de design,
   achados técnicos, regras dos testes automáticos (D14), ambiente, estado
   atual e plano com checkboxes.

2. LEIA AGORA, nesta ordem:
   a) docs/TESTBED_GUIA_COMPLETO.md — visão completa do testbed em
      09/11 (arquitetura, fases, config, saídas, medidas, histórico,
      resultados, limitações). É o documento que a usuária estudou e
      apresentou aos colegas e ao orientador; use os mesmos termos.
   b) docs/RELATORIO_SEMANA4_2026-11-09.md — último dia de trabalho.
   c) docs/DECISOES.md — leia a D15 (mobilidade) por inteiro; da D13
      (latência) leia o resumo final ("Situação da semana 3") e as
      confirmações; das demais basta o título (grep "^## D").
   NÃO leia README.md, RELATORIO_TECNICO.md, CONTEXTO_AGENTE.md,
   BIN_INTERFACE.md, third-party/ nem os testes antigos, a menos que a
   tarefa exija (estão desatualizados; CLAUDE.md vence).

3. ONDE PARAMOS (09/11/2026):
   - Semanas 1 e 2 na main (PRs #20 e #21). Semanas 3 e 4 em commits na
     branch semana1-execucao-real, SEM push e sem PR. Não afirme o estado do
     GitHub sem "git fetch".
   - Semana 3 (latência): casamento de pacotes pelo ID do IP, janela 5 s,
     latency-<origem>-<destino>.csv e bloco "latency" no summary.json.
   - Semana 4 (mobilidade): nodes[].mobility (trajectory com waypoints ou
     CSV), experiment.end (sender | duration), testbed/mobility.py
     (setPosition a cada 0,1 s após o início do sender), testbed/observer.py
     (routes.csv e signal.csv a cada 1 s), positions.csv, ended_by nos
     processos, sender de exemplo contínuo (50 ms até SIGTERM).
   - REAL: drone1 de 20 a 150 m em 30 s -> 407/698 entregues, perda a partir
     de 84 m, latência sobe antes (~70 m). Config padrão (10 s): 199/199.
   - TESTE AUTOMÁTICO: 217 OK.
   - Entre 09/11 e 16/11 a usuária só estudou o testbed e apresentou aos
     colegas e ao orientador. PERGUNTE se surgiram dúvidas, críticas ou
     pedidos dessa apresentação ANTES de seguir: eles podem mudar o plano.

4. PRÓXIMO: SEMANA 5 — latência e perda por janela de tempo × posições.
   A tabela "por distância" da seção 4.1 do relatório da semana 4 foi feita
   à mão; a semana 5 é o testbed gerá-la sozinho. Antes de codar, leve à
   usuária, de forma simples:
   - janela por tempo (ex.: 1 s) e/ou por faixa de distância (ex.: 10 m)?
     Distância de quem a quem (par sender/receiver; nós móveis)?
   - que métricas por janela (entregues/perdidos, mediana, máx, jitter?) e
     cruzar também com sinal (signal.csv) e rota/TQ (routes.csv)?
   - formato da saída (ex.: windows-<origem>-<destino>.csv) e resumo no
     terminal;
   - o que fazer com janelas sem pacotes.
   Para isso, leia só: scripts/testbed/latency.py, scripts/testbed/mobility.py,
   scripts/testbed/observer.py e scripts/run.py. Dados reais para conferir:
   logs/exp-08-mobilidade/2026-11-09_16-14-38/.
   Pendências que podem entrar: escolher batman_orig_interval_ms padrão para
   mobilidade; exercitar no real end: sender com timeout e waypoints_csv;
   push/PR das semanas 3 e 4 (só se ela pedir).

5. COMO TRABALHAR COM A USUÁRIA (importante):
   - Ela quer ENTENDER antes de avançar: explique de forma simples o que
     temos hoje, o que vai mudar, por quê, e de onde surgiu a necessidade.
     Use tabelas curtas e exemplos concretos. Não avance sem o "ok" dela em
     decisões de design. Responda exatamente o que ela perguntou.
   - Toda decisão ou explicação nova vira entrada em docs/DECISOES.md (D16,
     D17...) ANTES de seguir: contexto/quando surgiu, decisão, por quê, onde
     no código, implicações e, depois, a confirmação real.
   - Em todo relato, separe REAL (rodado por ela no host) de TESTE
     AUTOMÁTICO (sandbox, mocks, sintéticos). Só o real valida (D14).
   - Seja honesto: hipótese é hipótese; se algo não se confirmar, registre
     como "Correção" na decisão correspondente.
   - Ciclo: implementar + testes -> UM comando para o host + o que se espera
     ver (inclusive os resultados possíveis) -> ela roda e cola a saída ->
     você confere os arquivos gerados (e de forma independente) -> marca o
     checkbox no CLAUDE.md -> commit.
   - Commits só na branch atual, mensagem em português, com a linha
     Co-Authored-By. Não fazer merge/push sem pedido.
   - Documento para mostrar a alguém (orientador, colegas): .md em docs/.
     Ao fim do dia: relatório em docs/ e atualização deste prompt.

6. AMBIENTE (o shell do assistente é um sandbox Flatpak):
   - não tem docker, mn, tcpdump nem tshark; a execução real é sempre no
     host, pela usuária: sudo python3 scripts/run.py [config] 2>&1 | tee <log>
   - para cenários de teste, crie configs temporários poc_*.yml na raiz
     (só scripts/config.yml é versionado). Os poc_*.yml de 09/11 são da
     usuária (apresentação): não apague. Os da manhã de 09/11 não têm
     "end" e, com o sender contínuo, terminariam em "sender timed out".
   - os testes rodam aqui com pyyaml no scratchpad:
       pip install --target <scratchpad>/pylib pyyaml
       PYTHONPATH=<scratchpad>/pylib python3 -m unittest discover -s tests
   - resultados legíveis aqui em logs/<exp>/<data-hora>/; PCAPs podem ser
     lidos com scripts/testbed/pcap.py.
   - arquivos do root aparecem como "nfsnobody" aqui; é só o sandbox.
   - ao dar comandos para o host, não dependa da saída de "ls" (o terminal
     dela tem eza); use caminhos explícitos ou "command ls".

Confirme em poucas linhas que entendeu onde paramos, pergunte o que surgiu
na apresentação e, então, apresente de forma simples as decisões do item 4
antes de implementar.
```

## Variações

- Para abrir o PR das semanas 3 e 4 antes de seguir: troque a última frase
  por "Antes da semana 5, me explique como abrir o PR das semanas 3 e 4".
- Para só tirar dúvidas sobre o guia: troque a última frase por "Ainda não
  implemente nada; quero tirar dúvidas sobre o TESTBED_GUIA_COMPLETO.md".
