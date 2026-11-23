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
   a) docs/RELATORIO_2026-11-23.md — último dia (semanas 5 e 6, Random
      Waypoint, repetições), com o que foi validado no real.
   b) docs/GUIA_INTEGRACAO.md — o contrato atual (bloco commands, imagem
      própria, por que imagem própria): é a base do próximo passo.
   c) docs/DECISOES.md — leia D18 por inteiro (contrato) e os resumos
      "Decidido"/"Confirmação REAL" de D16, D17, D19 e D20; das demais
      basta o título (grep "^## D").
   d) docs/TESTBED_GUIA_COMPLETO.md só se precisar da visão geral.
   NÃO leia RELATORIO_TECNICO.md nem CONTEXTO_AGENTE.md (históricos),
   third-party/ nem os testes antigos, a menos que a tarefa exija.

3. ONDE PARAMOS (23/11/2026, fim do dia):
   - O plano de 6 semanas está concluído, inclusive o item "se der tempo".
     Além dele: Random Waypoint próprio (D19).
   - Git: main tem até a semana 4 (PR #22). O trabalho de 23/11 (semanas
     5 e 6, D19, D20) está na branch semana1-execucao-real; a usuária ia
     fazer push e abrir o PR ao encerrar o dia. CONFIRA com "git fetch" e
     "git log origin/semana1-execucao-real" antes de afirmar qualquer
     coisa sobre o GitHub.
   - Semana 5 (D16): windows-/distance-<origem>-<destino>.csv (bloco
     "analysis"), linha "first loss" no terminal.
   - Semana 6 (D18): bloco "commands" com marcadores {address}
     {destination} {port} {node_id} {mode} {data_dir} {output_dir}, sem
     shell; TESTBED_*; protocol-data/<nó>/ -> /opt/protocol/data (ro);
     out-<nó>/ <- /opt/protocol/out (cwd); status success = qualquer IPv4
     do sender para um receiver. Exemplo examples/echo (imagem
     drone-echo:latest).
   - D19: mobility.type random_waypoint (area, speed com mínimo > 0,
     pause, seed opcional, duration); caminho em trajectory-<nó>.csv.
   - D20: run.py --repeat N [--vary-seed] -> logs/<exp>/<data-hora>-
     campanha/rep-NN + statistics.json/csv (média, desvio, IC 95% t de
     Student).
   - REAL em 23/11: exp-11 (ping, dois sentidos), exp-12 (broadcast),
     exp-13 (unicast), exp-14 (echo), exp-01 (compatibilidade), exp-15
     (Random Waypoint), campanha de 5 do config padrão.
   - Ainda não real: TCP e protocolo real.
   - TESTE AUTOMÁTICO: 267 OK.

4. PRÓXIMO: PQ-EDHOC (a usuária tem os binários).
   Antes de qualquer coisa, peça e leve à usuária, de forma simples:
   - onde estão os binários (sugerir uma pasta do projeto, fora do Git se
     forem privados);
   - a saída de, no host:
       file <pasta>/* && docker run --rm -v "$(realpath <pasta>)":/b:ro \
         drone:latest sh -c 'for f in /b/*; do echo "== $f"; ldd "$f"; done'
     (estático ou dinâmico; qualquer "not found" = biblioteca faltando);
   - o --help de cada binário ou os argumentos que ela usou antes;
   - transporte (CoAP/UDP? porta?), IPv4 ou IPv6, quais chaves cada lado
     lê, se o initiator termina sozinho e se o responder roda até SIGTERM.
   Depois: imagem própria FROM drone:latest (decisão dela: manter a
   imagem própria), chaves em protocol-data/<nó>/, bloco commands, 1ª
   execução unicast parada a 20 m (end: sender), conferir out-<nó>/ e os
   dois sentidos. Um handshake gera poucos pacotes: discutir laço de
   handshakes ou --repeat para ter estatística.
   Outras pendências: escolher batman_orig_interval_ms padrão para
   mobilidade; observação em aberto do echo (pergunta ~1,66 ms x resposta
   ~0,17 ms); no fim do projeto, resumo no README da diferença Random
   Waypoint nativo x testbed (base: docs/MOBILIDADE_RANDOM_WAYPOINT.md,
   documento de estudo dela, NÃO commitar). Prazo: ~18/12/2026.

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
   - Documento que ela pede "só para estudar" NÃO é commitado: fica em
     docs/estudo/ ou é posto em .git/info/exclude (já estão lá:
     docs/estudo/, docs/MOBILIDADE_RANDOM_WAYPOINT.md,
     docs/ESTUDO_EXPERIMENTO_RANDOM_WAYPOINT.md).
   - Ao commitar, passe só os caminhos do trabalho: o índice tem arquivos
     que ela adicionou (docs/Plano_execucao, poc_*.yml, CSV de trajetória).

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
   - os poc_semana5_*.yml e poc_random_waypoint.yml (23/11) também são
     registro: não apague;
     bin/poc_ping_sender (sender que só roda ping) é ignorado pelo Git.
     Versionados como config: scripts/config.yml e examples/echo/config.yml.
   - resultados legíveis aqui em logs/<exp>/<data-hora>/; PCAPs podem ser
     lidos com scripts/testbed/pcap.py; tabelas da semana 5 de uma pasta
     antiga: cd scripts && python3 -m testbed.windows <pasta> --output-dir
     <scratchpad>.
   - arquivos do root aparecem como "nfsnobody" aqui; é só o sandbox.
   - ao dar comandos para o host, não dependa da saída de "ls" (o terminal
     dela tem eza); use caminhos explícitos ou "command ls".

Confirme em poucas linhas que entendeu onde paramos, confira o estado do
GitHub (git fetch), pergunte se há novidades (reunião do grupo,
orientador) e siga para o PQ-EDHOC pedindo as informações do item 4.
```

## Variações

- Se o push/PR não tiver sido feito: troque a última frase por "Antes de
  seguir, me explique como fazer push e abrir o PR do trabalho de 23/11".
- Para integrar um protocolo real: troque a última frase por "Quero
  integrar o <protocolo>; leia docs/GUIA_INTEGRACAO.md e me ajude a
  escrever a imagem e o bloco commands".
