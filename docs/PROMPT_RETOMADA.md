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
   a) docs/RELATORIO_2026-11-23.md — último dia de trabalho (semanas 5 e 6).
   b) docs/GUIA_INTEGRACAO.md — o contrato atual, do ponto de vista de quem
      integra um protocolo.
   c) docs/DECISOES.md — leia D16, D17 e D18 por inteiro (com as
      confirmações REAIS no fim de cada uma); das demais basta o título
      (grep "^## D").
   d) docs/TESTBED_GUIA_COMPLETO.md só se a tarefa for sobre ele (veja o
      item 4).
   NÃO leia RELATORIO_TECNICO.md, CONTEXTO_AGENTE.md (histórico),
   third-party/ nem os testes antigos, a menos que a tarefa exija.

3. ONDE PARAMOS (23/11/2026):
   - main: semanas 1 a 4 (PRs #20, #21, #22). Semanas 5 e 6 só na branch
     semana1-execucao-real, com commits locais SEM push. Não afirme o
     estado do GitHub sem "git fetch".
   - Semana 5 (D16): scripts/testbed/windows.py -> windows-/distance-
     <origem>-<destino>.csv (janela padrão 1 s, faixa 10 m; bloco
     "analysis" no config), linha "first loss" no terminal. REAL: exp-11
     (ping, dois sentidos, relay), exp-12 (broadcast 697/697, gcs via relay
     a ~111 m), exp-13 (unicast 627/697, troca de rota a ~120 m).
   - Semana 6 (D18): bloco "commands" com marcadores {address}
     {destination} {port} {node_id} {mode} {data_dir} {output_dir}, sem
     shell; TESTBED_*; protocol-data/<nó>/ -> /opt/protocol/data (ro);
     out-<nó>/ <- /opt/protocol/out (cwd); status success = qualquer IPv4
     do sender para um receiver; validate_scenario ajustado. Exemplo
     examples/echo (imagem drone-echo:latest). REAL: exp-14 (echo 50/50
     nos dois sentidos, ended_by self) e exp-01 (config padrão sem
     commands, 199/199, compatível).
   - D17: mobilidade nativa do Mininet-WiFi x thread do testbed; modelos
     aleatórios = tarefa extra, a usuária decide com o grupo.
   - Ainda não real: TCP e protocolo real (PQ-EDHOC).
   - TESTE AUTOMÁTICO: 243 testes; 1 falha alheia (scripts/teste.yml, da
     usuária, acusado por test_repository_contract): ela decide.

4. PRÓXIMO (pergunte à usuária a ordem):
   - Atualizar docs/TESTBED_GUIA_COMPLETO.md, se ainda não foi feito
     (confira a data no topo dele).
   - (Se der tempo) repetições: rodar o mesmo config N vezes e calcular
     média, desvio e IC 95% das métricas; levar as decisões à usuária
     antes de codar (onde ficam as N pastas, quais métricas, formato).
   - Pendências: escolher batman_orig_interval_ms padrão para mobilidade;
     observação em aberto do echo (pergunta ~1,66 ms x resposta ~0,17 ms,
     mesma taxa do Wi-Fi); PR das semanas 5 e 6 (só se ela pedir);
     decisão do grupo sobre modelos aleatórios (D17).
   - Prazo do projeto: ~18/12/2026.

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
   - os poc_semana5_*.yml (23/11) também são registro: não apague;
     bin/poc_ping_sender (sender que só roda ping) é ignorado pelo Git.
     Versionados como config: scripts/config.yml e examples/echo/config.yml.
   - resultados legíveis aqui em logs/<exp>/<data-hora>/; PCAPs podem ser
     lidos com scripts/testbed/pcap.py; tabelas da semana 5 de uma pasta
     antiga: cd scripts && python3 -m testbed.windows <pasta> --output-dir
     <scratchpad>.
   - arquivos do root aparecem como "nfsnobody" aqui; é só o sandbox.
   - ao dar comandos para o host, não dependa da saída de "ls" (o terminal
     dela tem eza); use caminhos explícitos ou "command ls".

Confirme em poucas linhas que entendeu onde paramos, pergunte se há
novidades (reunião do grupo sobre a D17, binários do PQ-EDHOC) e qual
item do 4 ela quer seguir.
```

## Variações

- Para abrir o PR das semanas 5 e 6 antes de seguir: troque a última frase
  por "Antes de seguir, me explique como fazer push e abrir o PR das
  semanas 5 e 6".
- Para integrar um protocolo real: troque a última frase por "Quero
  integrar o <protocolo>; leia docs/GUIA_INTEGRACAO.md e me ajude a
  escrever a imagem e o bloco commands".
