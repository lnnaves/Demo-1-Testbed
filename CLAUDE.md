# CLAUDE.md — contexto e plano do Demo-1-Testbed

Carregado automaticamente em toda sessão. Fonte de verdade sobre decisões e
plano. Onde conflitar com `docs/CONTEXTO_AGENTE.md`, **este arquivo vence**.
Atualize a seção "Estado atual" e os checkboxes ao concluir cada item.

## Objetivo e prazo

Testbed agnóstico de protocolo para drones (Containernet + Mininet-WiFi +
BATMAN-adv + tcpdump). Prazo: **~1,5 mês a partir de 2026-11-03 (até ~2026-12-18)**.
Prioridades: unicast e broadcast funcionando; tempo de execução; latência
fim-a-fim; entrega/perda; mobilidade.

## Fronteira de responsabilidade (decidido)

- **Testbed:** cenário, rádio, BATMAN, IPs, portas, espera da malha, ciclo de
  vida dos processos, captura, coleta (stdout/stderr/arquivos), métricas,
  relatórios, limpeza.
- **Usuário:** o programa do protocolo, bibliotecas, chaves, certificados e uma
  imagem Docker própria `FROM drone:latest` (campo `nodes[].image`). O testbed
  **não** instala dependências de protocolo.
- Lista mínima do usuário: (1) imagem com dependências; (2) programa Linux, sem
  interação, em foreground; (3) aceitar IP/porta na linha de comando em
  qualquer formato.

## Decisões de design

- **Contrato novo (semana 6):** o usuário escreve o comando no config com
  marcadores preenchidos pelo testbed: `{address}`, `{destination}`, `{port}`,
  `{node_id}`, `{mode}`, `{data_dir}`, `{output_dir}`. Substitui o contrato fixo
  `--address/--destination/--port` e o MEL-03 do doc de requisitos.
- Variáveis de ambiente `TESTBED_NODE_ID`, `TESTBED_ROLE`, `TESTBED_MODE`,
  `TESTBED_DATA_DIR`, `TESTBED_OUTPUT_DIR` (opcionais para o programa).
- Diretórios: `/opt/protocol/data` (ro, opcional, de `protocol-data/<node_id>/`),
  `/opt/protocol/out` (rw, copiado para a pasta da execução; cwd do processo).
- Encerramento pelo testbed (SIGTERM/timeout) é **normal**, qualquer exit code;
  registrar `ended_by: self|testbed`.
- Captura de todo o tráfego em `bat0` por padrão; filtro BPF opcional.
- Espera da malha: ping sender→cada receiver até `network_ready_timeout_seconds`,
  depois `ip neigh flush dev bat0`; timeout = falha clara.
- `drain_seconds` após o sender terminar, antes de parar receivers/captura.
- Latência (D13, 09/11): latência **de rede** fim-a-fim (`bat0` → `bat0`,
  todos os saltos), não de programa a programa. Casar pacotes entre PCAPs
  pelo **ID do IP** + IPs + protocolo + portas + tamanho, nos dois sentidos,
  todo pacote IP entre participantes; janela máxima de 5 s; leitura em
  Python puro (sem tshark). O ID sobe com saltos aleatórios (volta a zero a
  cada ~2.800 pacotes): ordem vem do horário. Relógio compartilhado entre
  containers. Broadcast: por receiver.
- **O testbed expõe o alcance, não garante conectividade** (decidido em
  2026-11-05). O usuário escolhe as posições para descobrir onde o protocolo
  dele deixa de funcionar bem; o testbed registra o que aconteceu (perda,
  latência, rotas). Não calibrar nem alterar o rádio do Mininet-WiFi. O cenário
  padrão é só exemplo e não precisa ter multi-hop. `nodes[].range` foi removido
  (aceito com aviso, ignorado) porque o wmediumd não o usa.
- Mobilidade: tipos `static` e `trajectory` (waypoints no YAML ou CSV).
  Plano B/talvez A: thread do testbed interpola e chama `setPosition()`.
  Modelos aleatórios só se sobrar tempo.

## Regras antigas revogadas (de CONTEXTO_AGENTE.md / test_repository_contract.py)

- Contrato fixo `--address/--destination/--port` e proibição de argumentos
  dinâmicos → substituídos pelo comando com marcadores.
- "Nunca gerar YAML" → campanhas poderão salvar a config usada por ponto (fora
  do escopo atual).
- Continuam valendo: agnóstico de protocolo (nunca interpretar payload/stdout),
  não alterar `third-party/containernet/`, honestidade (sem pré-requisitos =
  NÃO EXECUTADO; `interval_*` não é latência), rodar testes antes de concluir.

## Achados técnicos (verificados em execução real)

- `DockerSta.popen()` usa `docker exec -t`: TTY corrompe binário, junta stderr
  em stdout e **não repassa sinais**. Resolvido: processos via
  `network.container_popen` (nsenter -m -u -i -n --root --wd, env do container)
  e captura via `nsenter -n` no host (`node.pid` = PID do container).
- Processos via nsenter não ficam no cgroup do container (limite de memória do
  nó não se aplica; invisíveis ao `docker stats`).
- bash do `ubuntu:24.04` emite `\x1b[?2004h` na saída de `node.cmd()`;
  sentinelas são casadas como linha inteira em qualquer posição.
- **BATMAN Bridge Loop Avoidance** (ligado por padrão) descarta broadcasts/ARP
  por ~30 s após o start. Desligado por padrão (`batman_bridge_loop_avoidance`):
  malha pronta em ~10 s em vez de ~28 s.
- **`batman_orig_interval_ms`** muda a reação à mobilidade: com 1000 o BATMAN
  não trocou para o relay antes de perder o enlace; com 200 trocou em x≈62 m.
- **Mobilidade funciona com `setPosition()`** em `DockerSta` + wmediumd
  interference: sinal cai de -52 dBm (15 m) a -90 dBm (~60 m), rota muda para
  via relay e volta. Plano A da semana 4 validado.
- **`range` não tem efeito** no modo interference: passado ao `addStation` só
  fixa `wintf.range` (static_range); potência só mudaria via `setRange()`.
  Alcance direto: **não é ~60 m** (correção 09/11): broadcast/unicast com
  20/20 entregues a 50, 58, 62 e 70 m; em movimento, 100% até ~84 m,
  perda crescente depois, pacotes isolados ainda a 150 m (sem corte
  abrupto; latência sobe antes da perda); na PoC o ping só falhou quase sempre
  de ~72 m em diante (os -90 dBm do log são o último valor recebido). Sinal medido
  (poc_200ms.log): 15 m -53..-74, 30 m -68..-82, 45 m -82..-89, 60 m -90 dBm;
  ping falhou com enlace de ~42–57 m. No cenário padrão (0/20/40) todos são
  vizinhos diretos (aceito: é só exemplo).
- `batctl o` mantém a última rota durante desconexão (até o purge); ao logar
  topologia, guardar também o last-seen. Valores de sinal 0/+1/+3/-7 dBm muito
  perto de outro nó são quirk do wmediumd.
- `tc netem` em `bat0` age antes do tcpdump (perda/atraso invisíveis); se usar,
  aplicar na `wlan0`.
- wmediumd tem aleatoriedade não controlável: mesma config = mesmo cenário,
  não mesmos resultados → repetições + estatística.
- Arquivos de root aparecem como `nfsnobody` **só dentro do sandbox Flatpak**
  (mapeamento de uid); no host o dono é `root` (apagar exige sudo). Não é
  root squash. Artefatos antigos soltos em `logs/` são de versões anteriores.
- **BATMAN DAT (Distributed ARP Table)** responde ARP localmente: no PCAP do
  sender o ARP reply vem 8 µs após o request e o ARP nem aparece no receiver.
  `ip neigh flush` limpa só o ARP do kernel, não o cache DAT da espera da malha.
- `third-party/containernet/.git` aponta para repositório inexistente (inofensivo).

## Testes automáticos (D14)

- Só execução **REAL** no host valida; testes automáticos (sandbox, mocks,
  sintéticos) só detectam regressão. Todo relato separa REAL × TESTE
  AUTOMÁTICO.
- `tests/` será removida ao fim do projeto: o código do testbed nunca pode
  depender de `tests/`.

## Ambiente de trabalho

- O shell do Claude (sandbox Flatpak) **não tem** docker, mn, tcpdump. Suíte
  completa roda aqui com pyyaml instalado no scratchpad:
  `pip install --target <scratchpad>/pylib pyyaml` e
  `PYTHONPATH=<scratchpad>/pylib python3 -m unittest discover -s tests`
  (o scratchpad muda por sessão; reinstalar se faltar).
- Execução real: **a usuária roda no host** e cola a saída (ou avisa e o Claude
  lê arquivos gravados com `tee`, ex.: `poc_*.log`, `logs/*.json`).
- Ciclo: Claude implementa + testes → entrega **um** comando e o que se espera
  ver → usuária roda → próxima etapa.
- Comandos no host: `sudo python3 scripts/run.py` (limpeza prévia é automática);
  diagnóstico de rede/mobilidade: `sudo python3 scripts/poc_mobility.py [--convergence-only] [--batctl "bl 0"] [--orig-interval 200]`.

## Estado atual (2026-11-08)

- **Semana 1 concluída**, enviada e mergeada na `main` pelo PR #20 (merge
  `3c795fc`, feito pela usuária no GitHub). Suíte na época: 157 testes OK.
- Semana 2 (commits de 05/11) na mesma branch `semana1-execucao-real`, após o
  merge; falta push + novo PR. **Sempre rodar `git fetch` antes de afirmar o
  estado do GitHub** (a cópia local de `origin/*` fica desatualizada).
- Confirmado no host: unicast e broadcast em `success`; receivers encerram com
  "Receiver finished"; "Mesh ready after 10.1s".
- `scripts/config.yml` versionado: unicast, drone1→gcs, posições 0/20/40,
  `network_ready_timeout_seconds: 60`, `drain_seconds: 1`, BLA desligado,
  `batman_orig_interval_ms: 1000`.
- Fora do Git: `docs/Plano_execucao` (arquivo da usuária, plano semanal igual
  ao deste arquivo) e `poc_*.log` (ignorados).
- 2026-11-05: `range` removido (aviso se presente), README/RELATORIO_TECNICO
  atualizados sobre alcance (confirmado no host: success, malha 10.0 s).
- 2026-11-05: pasta por execução implementada (D9): `testbed/rundir.py`,
  `run.py --output-dir`, bloco `output:` removido, campos desconhecidos no
  config viram erro. Suíte: 167 testes OK. Confirmado no host (2 execuções →
  2 pastas, config copiado idêntico, success). Arquivos são do `root`;
  apagar exige sudo.
- Tempos implementados (D10): `timing.json` por execução, instantes por processo,
  espera da malha com pausa 0,2 s (D8; precisão real ~1,2 s, ver correção).
  Suíte: 175 testes OK. Confirmado no host: total 28,4 s = prepare_host 3,4 +
  build_network 7,3 + wait_network 10,2 + execute_protocol 2,5 + cleanup 5,0;
  pacote do PCAP cai entre started_at/ended_at do sender (mesma base de tempo).
- Metadados (D11) confirmados no host: commit lido sob sudo, sha256 batem,
  BATMAN-adv 2025.5, kernel 7.0.0-42. Suíte 181 OK.
- **Achado:** imagem `drone:latest` criada 2026-09-23 17:35 -03, antes da última
  mudança do Dockerfile (7c9625e, 18:03 -03): pode não refletir o Dockerfile
  atual. Sugerido rebuild (`scripts/build-docker.sh`); usuária não decidiu.
- PCAP por participante + `capture.filter` (D12) confirmados no host. Suíte 187
  OK. **Semana 2 concluída.** Imagem refeita (id d7f035f1…, 2026-11-05 15:35).
- (05/11) Próximo era a semana 3 (latência). Primeira medida manual: UDP no PCAP do sender
  .551854 → no do gcs .553587 = 1,73 ms.
- **Semana 2 mergeada na `main` pelo PR #21** (`98b07ce`), confirmado com
  `git fetch` em 2026-11-08.
- **2026-11-09 (tarde): semana 4 implementada e confirmada no host** (D15):
  `experiment.end`, `nodes[].mobility`, `testbed/mobility.py` (setPosition a
  cada 0,1 s após o início do sender), `testbed/observer.py` (routes.csv e
  signal.csv a cada 1 s), sender de exemplo contínuo (50 ms até SIGTERM,
  erros de envio contados), `duration_seconds` conta do início do sender,
  `config.yml` padrão com `end: duration` e 10 s. Real (`poc_mobilidade.yml`,
  20→150 m em 30 s): 407/698, perda começa a 84 m, latência sobe antes.
  Suíte 217 OK. Falta semana 5: latência por janela × posições.
- **2026-11-09: semana 3 implementada** (D13, D14): `testbed/pcap.py` +
  `testbed/latency.py`, casamento pelo ID do IP, `latency-<orig>-<dest>.csv`
  + bloco `latency` no summary. REAL confirmado: unicast e broadcast (por
  receiver), 20 m a 70 m. **Perda NÃO confirmada no real** (nenhuma perda
  até 70 m; degradação vira atraso): validar na mobilidade. Suíte 202 OK.
  Configs temporários `poc_*.yml` na raiz (não versionados).
- 2026-11-08: semana 3 em discussão, nada implementado. Ver D13: proposta de
  casar pacotes pelo **ID do cabeçalho IP** em vez de hash do payload (mesmo
  ID 27575 nos dois PCAPs reais); talvez dispensar `tshark` (Python puro).
  Aguardando ok da usuária.

## Pendências fora do plano semanal

- Escolher `batman_orig_interval_ms` padrão para cenários com mobilidade.
- Guia de integração (semana 6) deve dizer que a imagem do usuário precisa de
  `ping` (usado pela espera da malha; `FROM drone:latest` já tem).
- Em aberto (D8): encurtar `ping -W` para medir a espera da malha em décimos.
- `validate_scenario.py` ainda exige `exit_code == 0` dos receivers; pela
  decisão de design, encerramento pelo testbed é normal com qualquer código
  (registrar `ended_by`). Ajustar junto com a semana 6.
- README, `bin/BIN_INTERFACE.md` e `docs/CONTEXTO_AGENTE.md` ainda descrevem o
  comportamento antigo em pontos (processos, BLA, fases novas).

## Plano (marcar ao concluir; cada fase fecha com execução real confirmada)

### Semana 1 — fazer funcionar
- [x] Processos via `nsenter` (sinais reais, stdout/stderr separados)
- [x] Espera da malha (`wait_network`) + flush ARP + timeout configurável
- [x] BATMAN: BLA desligado, `orig_interval` configurável (~28 s → ~10 s)
- [x] Limpeza prévia automática (`mn -c`, containers `mn.*`)
- [x] `drain_seconds`
- [x] Prova de conceito de mobilidade (DockerSta + adhoc BATMAN + movimento)
- [x] Marco: unicast e broadcast reais em `success`

### Semana 2 — saídas e tempo
- [x] Pasta por execução `logs/<experimento>/<data-hora>/` + cópia da config
- [x] Metadados: commit, digest da imagem, sha256 dos binários, kernel, seeds
- [x] Tempos: experimento, cada fase, cada processo
- [x] PCAP por participante (sender e receivers) + filtro configurável

### Semana 3 — latência
- [x] Casamento de pacotes pelo ID do IP (Python) → latência por pacote (CSV),
      mín/mediana/média/p95/p99/máx, jitter, entrega/perda, rajada de perda,
      fora de ordem, duplicados; por sentido e por receiver
- [x] Testes com PCAPs sintéticos (TESTE AUTOMÁTICO, D14)
- [x] Perda confirmada em execução real (mobilidade, 09/11: 407/698)

### Semanas 4–5 — mobilidade
- [x] `nodes[].mobility`: `static` | `trajectory` (waypoints/CSV)
- [x] Encerrar por duração ou por fim do sender (`experiment.end`; real: duration)
- [x] Log de posições e log de `batctl o` ao longo do tempo (saltos por instante)
      + `signal.csv` (`iw station dump`)
- [ ] Latência por janela de tempo cruzada com posições

### Semana 6 — contrato e documentação
- [ ] Comando com marcadores, `data`/`out`, variáveis de ambiente
- [ ] Exemplo completo + guia "integre seu protocolo"
- [ ] Atualizar README, BIN_INTERFACE.md, CONTEXTO_AGENTE.md, testes de contrato
- [ ] (se der tempo) repetições com média, desvio e IC 95%

### Fora de escopo
Script de instalação, Makefile, CI, campanhas, gráficos automáticos, eventos do
protocolo (MET-07), CPU/memória, netem, topologias geradas, README em inglês,
CITATION, SUMO, animação, modelos de mobilidade aleatórios.

## Referências

- `docs/PROMPT_RETOMADA.md` (atualizado 09/11): prompt para iniciar uma sessão nova (o que ler,
  onde paramos, como trabalhar). Atualizar ao fim de cada dia.
- `docs/RELATORIO_<data>.md`: relatório de cada dia de trabalho.

- **`docs/DECISOES.md`: registro de decisões com explicação completa**
  (contexto/quando surgiu, decisão, onde no código, implicações). **Toda decisão
  nova ou explicação dada à usuária sobre o porquê de algo deve virar entrada
  nesse arquivo antes de seguir** — ela precisa disso depois.

- Doc de requisitos da usuária (IDs MOB-, MET-, MEL-, PRA-, AUT-, COMP-): base
  do plano; análise já feita e incorporada acima (MEL-03, MEL-05, MEL-07 e
  MET-09 revisados; 26 P0 reduzidos ao plano acima).
- `docs/RELATORIO_TECNICO.md`: descrição técnica do código atual.
- `bin/BIN_INTERFACE.md`: contrato atual (será substituído na semana 6).
