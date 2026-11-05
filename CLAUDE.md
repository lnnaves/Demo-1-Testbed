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
- Latência: casar pacotes entre PCAPs (sender × receiver) pelo hash do payload
  com `tshark` (só UDP; tshark remonta fragmentos). Relógio é compartilhado
  entre containers, timestamps são comparáveis. Broadcast: por receiver.
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
  Alcance direto real ~60 m (logDistance 3.5, 10 dBm, -91 dBm). Sinal medido
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
- `logs/` é gravado como `nfsnobody` (provável root squash); artefatos antigos
  em `logs/` (inclusive `unicast-demo-01/`) são de versões anteriores.
- `third-party/containernet/.git` aponta para repositório inexistente (inofensivo).

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

## Estado atual (2026-11-05)

- **Semana 1 concluída e commitada** na branch `semana1-execucao-real`
  (ainda não mergeada na `main`, nada enviado ao GitHub). Suíte: 157 testes OK.
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
  2 pastas, config copiado idêntico, success). Arquivos novos saem com dono
  `root` (não `nfsnobody`); apagar exige sudo.
- Próximo na semana 2: tempos (experimento, fases, processos, com hora real) +
  espera da malha a cada ~0,2 s (D8); depois metadados; depois PCAP por nó.

## Pendências fora do plano semanal

- Escolher `batman_orig_interval_ms` padrão para cenários com mobilidade.
- Guia de integração (semana 6) deve dizer que a imagem do usuário precisa de
  `ping` (usado pela espera da malha; `FROM drone:latest` já tem).
- Semana 2: espera da malha passa a testar a cada ~0,2 s (DECISOES D8).
- `validate_scenario.py` ainda exige `exit_code == 0` dos receivers; pela
  decisão de design, encerramento pelo testbed é normal com qualquer código
  (registrar `ended_by`). Ajustar junto com a semana 6.
- README, `bin/BIN_INTERFACE.md` e `docs/CONTEXTO_AGENTE.md` ainda descrevem o
  comportamento antigo em pontos (processos, BLA, fases novas).
- Merge/PR da branch `semana1-execucao-real`.

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
- [ ] Metadados: commit, digest da imagem, sha256 dos binários, kernel, seeds
- [ ] Tempos: experimento, cada fase, cada processo
- [ ] PCAP por participante (sender e receivers) + filtro configurável

### Semana 3 — latência
- [ ] Casamento de pacotes por hash (tshark) → latência por pacote (CSV),
      média/mediana/p95/máx, entrega/perda; broadcast por receiver
- [ ] Testes com PCAPs sintéticos

### Semanas 4–5 — mobilidade
- [ ] `nodes[].mobility`: `static` | `trajectory` (waypoints/CSV)
- [ ] Encerrar por duração ou por fim do sender
- [ ] Log de posições e log de `batctl o` ao longo do tempo (saltos por instante)
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

- **`docs/DECISOES.md`: registro de decisões com explicação completa**
  (contexto/quando surgiu, decisão, onde no código, implicações). **Toda decisão
  nova ou explicação dada à usuária sobre o porquê de algo deve virar entrada
  nesse arquivo antes de seguir** — ela precisa disso depois.

- Doc de requisitos da usuária (IDs MOB-, MET-, MEL-, PRA-, AUT-, COMP-): base
  do plano; análise já feita e incorporada acima (MEL-03, MEL-05, MEL-07 e
  MET-09 revisados; 26 P0 reduzidos ao plano acima).
- `docs/RELATORIO_TECNICO.md`: descrição técnica do código atual.
- `bin/BIN_INTERFACE.md`: contrato atual (será substituído na semana 6).
