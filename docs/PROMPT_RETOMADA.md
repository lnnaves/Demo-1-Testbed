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
   a) docs/RELATORIO_2026-11-09.md — resumo do último dia (semana 3).
   b) docs/DECISOES.md — leia a D13 (latência) e a D14 (testes) por inteiro;
      das D1 a D12 basta o título de cada uma (grep "^## D"), e abra a que a
      tarefa exigir. Para a mobilidade, releia também D3, D6 e D7.
   c) docs/HIPOTESES_2026-11-09.md — hipóteses abertas sobre reenvios do
      Wi-Fi/BATMAN (a perda virou atraso nas execuções reais).
   NÃO leia README.md, RELATORIO_TECNICO.md, CONTEXTO_AGENTE.md,
   BIN_INTERFACE.md, third-party/ nem os testes antigos, a menos que a
   tarefa exija. CONTEXTO_AGENTE.md e BIN_INTERFACE.md estão desatualizados
   (contrato antigo); onde conflitarem, CLAUDE.md vence.

3. ONDE PARAMOS:
   - Semanas 1 e 2 na main (PRs #20 e #21). Semana 3 em commits na branch
     semana1-execucao-real (80e2d02, a231129 e o deste relatório), SEM push
     e sem PR. Não afirme o estado do GitHub sem "git fetch".
   - Semana 3 implementada: scripts/testbed/pcap.py (leitor de PCAP em
     Python) e scripts/testbed/latency.py (casamento pelo ID do IP + IPs +
     protocolo + portas + tamanho, janela de 5 s, dois sentidos, por fluxo).
     Saídas: latency-<origem>-<destino>.csv e bloco "latency" no
     summary.json. bin/sender envia 20 pacotes iguais, 50 ms entre eles.
   - REAL confirmado: latência e entrega em unicast e broadcast (por
     receiver), 20 m a 70 m. PERDA NÃO CONFIRMADA NO REAL: até 70 m nenhum
     pacote se perdeu (a degradação aparece como atraso: máx e jitter
     sobem). Checkbox "Perda confirmada em execução real" está aberto.
   - TESTE AUTOMÁTICO: 202 OK.
   - Arquivos temporários poc_*.yml na raiz (não versionados).

4. PRÓXIMO: SEMANAS 4–5, MOBILIDADE. Primeiro objetivo combinado: validar a
   perda real com um nó que começa perto (a malha fica pronta) e se afasta
   durante a execução. Antes de codar, leve à usuária as decisões de
   design, de forma simples:
   - formato de nodes[].mobility: static | trajectory (waypoints no YAML ou
     CSV); quem move é uma thread do testbed chamando setPosition()
     (validado na PoC: scripts/poc_mobility.py);
   - encerrar por duração ou pelo fim do sender (o sender de exemplo dura
     ~1 s: para mobilidade precisará enviar por mais tempo, sem mudar os
     argumentos);
   - log de posições e de "batctl o" ao longo do tempo (com last-seen);
   - latência/perda por janela de tempo cruzada com as posições;
   - batman_orig_interval_ms padrão para mobilidade (1000 não trocou de
     rota a tempo na PoC; 200 trocou).
   Para isso, leia só: scripts/poc_mobility.py, scripts/testbed/network.py,
   scripts/testbed/runner.py, scripts/testbed/config.py e
   scripts/testbed/latency.py.

5. COMO TRABALHAR COM A USUÁRIA (importante):
   - Ela quer ENTENDER antes de avançar: explique de forma simples o que
     temos hoje, o que vai mudar, por quê, e de onde surgiu a necessidade.
     Use tabelas curtas e exemplos concretos. Não avance sem o "ok" dela em
     decisões de design. Responda exatamente o que ela perguntou.
   - Toda decisão ou explicação nova vira entrada em docs/DECISOES.md (D15,
     D16...) ANTES de seguir: contexto/quando surgiu, decisão, por quê, onde
     no código, implicações e, depois, a confirmação real.
   - Em todo relato, separe REAL (rodado por ela no host) de TESTE
     AUTOMÁTICO (sandbox, mocks, sintéticos). Só o real valida (D14).
   - Seja honesto: hipótese é hipótese; se algo prometido não se confirmar,
     registre como "Correção" na decisão correspondente.
   - Ciclo: implementar + testes -> UM comando para o host + o que se espera
     ver (inclusive os resultados possíveis) -> ela roda e cola a saída ->
     você confere os arquivos gerados (e confere de forma independente) ->
     marca o checkbox no CLAUDE.md -> commit.
   - Commits só na branch atual, mensagem em português, com a linha
     Co-Authored-By. Não fazer merge/push sem pedido.
   - Quando ela pedir um documento para mostrar a alguém (orientador,
     colega), escreva um .md breve em docs/.

6. AMBIENTE (o shell do assistente é um sandbox Flatpak):
   - não tem docker, mn, tcpdump nem tshark; a execução real é sempre no
     host, pela usuária, com: sudo python3 scripts/run.py [config] 2>&1 | tee <log>
   - para cenários de teste, crie configs temporários poc_*.yml na raiz
     (só scripts/config.yml é versionado).
   - os testes rodam aqui com pyyaml no scratchpad:
       pip install --target <scratchpad>/pylib pyyaml
       PYTHONPATH=<scratchpad>/pylib python3 -m unittest discover -s tests
   - os resultados ficam legíveis aqui em logs/<exp>/<data-hora>/; os PCAPs
     podem ser lidos com scripts/testbed/pcap.py (ou struct).
   - arquivos do root aparecem como "nfsnobody" aqui; é só o sandbox.
   - ao dar comandos para o host, não dependa da saída de "ls" (o terminal
     dela tem ls com cores, eza); use caminhos explícitos ou "command ls".

Confirme em poucas linhas que entendeu onde paramos e o que vem a seguir,
e então me apresente, de forma simples, as decisões do item 4 antes de
implementar.
```

## Variações

- Para abrir o PR da semana 3 antes de seguir: troque a última frase por
  "Antes da mobilidade, me explique como abrir o PR da semana 3".
- Para só tirar dúvidas: troque a última frase por "Ainda não implemente
  nada; quero tirar dúvidas".
