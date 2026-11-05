# Registro de decisões

Cada entrada responde: **quando e por que surgiu**, **o que foi decidido**,
**onde está no código** e **o que isso implica**. Entradas novas vão no fim.
Os achados técnicos resumidos ficam também no `CLAUDE.md`. Este arquivo
guarda a explicação completa.

---

## D1. Fronteira de responsabilidade entre testbed e usuário (03/11/2026)

**Contexto.** O documento de requisitos misturava responsabilidades do testbed
com as do protocolo testado.

**Decisão.**
- **O testbed cuida de:** cenário, rádio, BATMAN, IPs, portas, espera da
  malha, ciclo de vida dos processos, captura, coleta, métricas, relatórios e
  limpeza.
- **O usuário cuida de:** o programa do protocolo, as bibliotecas, as chaves,
  os certificados e uma imagem Docker própria `FROM drone:latest`.

O testbed nunca instala dependências de protocolo nem interpreta o payload.

---

## D2. Processos rodam via `nsenter`, não via `docker exec` (03/11/2026)

**Contexto.** Na primeira execução real, o PCAP saiu corrompido e o receiver
não encerrava ao receber o sinal de parada. A causa é o `DockerSta.popen()` do
Containernet, que usa `docker exec -t`. O TTY desse comando mistura texto com
dados binários, junta stderr ao stdout e não repassa sinais ao processo.

**Decisão.** Os processos do protocolo rodam com `nsenter`, entrando nos
namespaces do container. O `tcpdump` roda no host, dentro da rede do container
(`nsenter -n`).

**Onde.** `container_popen` em `scripts/testbed/network.py` e `Capture.start`
em `scripts/testbed/metrics.py`.

**Implicação.** Esses processos ficam fora do cgroup do container. O limite de
memória do nó não se aplica a eles, e eles não aparecem no `docker stats`.

---

## D3. Espera da malha antes do protocolo (03/11/2026)

**Contexto: quando surgiu.** Nas primeiras execuções reais, nenhum pacote era
capturado. Assim que os containers subiam, o testbed já iniciava o sender, mas
o BATMAN ainda não tinha descoberto os vizinhos. O pacote se perdia.

**Decisão.** Criar uma fase `wait_network` entre "montar a rede" e "rodar o
protocolo". O objetivo é **garantir que o protocolo só comece com a rede
funcionando**.

**Onde.**
- `scripts/testbed/network.py`:
  - `_ping`: executa `ping -c 1 -W 1 <IP>` dentro de um container.
  - `wait_for_network`: chama `_ping` em loop, mede o tempo e limpa o ARP.
- `scripts/run.py`: chama a fase `wait_network` logo após `build_network`.

**Como funciona.**
1. Marca o início com `time.monotonic()`, um relógio que só avança.
2. Do **nó** sender, faz ping para cada receiver. O ping é o comando `ping` do
   Linux, que vem da imagem (`iputils-ping`), **não** o programa `bin/sender`
   do usuário.
3. Se algum receiver não responde, tenta de novo. Quando todos responderem,
   imprime `Mesh ready after X s`.
4. Se não responderem dentro de `network_ready_timeout_seconds` (60 s no
   config atual), falha com `network not ready: <sender> could not reach
   <receiver>`.
5. Ao final, executa `ip neigh flush` em todos os nós.

**Por que não interfere no protocolo do usuário.**
- As fases são sequenciais:
  `montar rede → espera (pings) → limpar ARP → iniciar captura → protocolo`.
  Nenhum ping roda durante o protocolo.
- O ping ensina aos nós o MAC dos vizinhos, que fica guardado na tabela ARP.
  O `ip neigh flush` apaga essa tabela, e o protocolo começa como se os pings
  não tivessem existido.
- Os pings não aparecem no PCAP, porque a captura começa depois.
- O que permanece, de propósito, são as rotas aprendidas pelo BATMAN. Esse é o
  objetivo da espera.

**Implicações.**
- A imagem do usuário precisa ter o comando `ping`. Uma imagem
  `FROM drone:latest` já tem. Sem ele, a espera falha com `network not ready`.
  Isso deve entrar no guia de integração (semana 6).
- Nós fora de alcance também resultam em `network not ready`. É uma resposta
  válida ("não há caminho"), não um defeito. Ver D7.

---

## D4. Bridge Loop Avoidance (BLA) do BATMAN desligado por padrão (03/11/2026)

**Contexto.** A espera da D3 levava ~28 s. O BLA, ligado por padrão no
BATMAN, descarta broadcasts (inclusive ARP) por ~30 s após o início, enquanto
procura outros gateways. O BLA só faz sentido quando a malha está ligada por
bridge a uma rede cabeada, o que não acontece no cenário.

**Decisão.** Desligado por padrão (`wireless.batman_bridge_loop_avoidance:
false`). A malha passou a ficar pronta em ~10 s.

**Onde.** `_configure_batman` em `scripts/testbed/network.py` (`batctl bl`).

---

## D5. `drain_seconds` depois do sender (03/11/2026)

**Contexto.** Os receivers eram parados logo que o sender terminava, e as
últimas mensagens ainda em trânsito podiam se perder.

**Decisão.** Esperar `experiment.drain_seconds` (padrão 1 s) depois que o
sender termina, antes de parar os receivers e a captura.

**Onde.** `execute_protocol` em `scripts/testbed/runner.py`.

---

## D6. Intervalo de anúncios do BATMAN configurável (03/11/2026)

**Contexto.** Na prova de conceito de mobilidade, com o padrão de 1000 ms o
BATMAN não trocou para a rota via relay antes de perder o enlace direto. Com
200 ms, trocou em x≈62 m.

**Decisão.** Parâmetro `wireless.batman_orig_interval_ms` (padrão 1000). A
escolha do padrão para cenários com mobilidade ficou **pendente** (semanas
4–5).

**Onde.** `_configure_batman` em `scripts/testbed/network.py` (`batctl it`).

---

## D7. O testbed expõe o alcance, não garante conectividade; `range` removido (05/11/2026)

**Contexto: quando surgiu.** A prova de conceito mostrou dois fatos:
- o campo `nodes[].range: 25` do config não tinha efeito;
- o alcance real é de ~60 m, então no cenário padrão (0/20/40 m) todos os nós
  se alcançam diretamente, sem multi-hop.

A primeira proposta foi reposicionar os nós para forçar multi-hop. Ela foi
recusada.

**Decisão.**
- **O testbed não garante que a rede funcione. Ele mostra o que acontece.**
  Cada usuário define as posições para descobrir a partir de que distância o
  protocolo dele deixa de funcionar bem.
- Não calibramos nem alteramos o rádio do Mininet-WiFi: ele é usado como é.
- O cenário padrão é só um exemplo e não precisa ter multi-hop.
- O `range` foi removido. Configs antigas que ainda o têm são aceitas, com o
  aviso `nodes[i].range is ignored`.

**Por que o `range` não tinha efeito.** No modo interference, quem decide se
dois nós se ouvem é o wmediumd. Ele calcula o sinal a partir de `txpower`, do
modelo de propagação e da distância, e descarta o que fica abaixo de
`noise_threshold_dbm`. No Mininet-WiFi, passar `range` ao `addStation` só grava
o número e trava o cálculo interno de alcance. A potência só mudaria pelo
método `setRange()`, que o testbed não usa.

**Sinal medido** (10 dBm, logDistance 3.5, limiar -91 dBm, fading 3; log
`poc_200ms.log`):

| Distância | Sinal |
|---|---|
| 15 m | -53 a -74 dBm |
| ~30 m | -68 a -82 dBm |
| ~45 m | -82 a -89 dBm |
| ~60 m | ~-90 dBm (no limiar) |

**Onde.** `scripts/testbed/config.py` (aviso), `scripts/run.py` (imprime o
aviso), `scripts/testbed/network.py` (o `range` deixou de ser repassado),
seção "Alcance do rádio e posições" no README.

**Confirmação real.** Execução em 05/11 sem `range`: `Mesh ready after 10.0s`
e `Experiment completed successfully.`

---

## D8. Precisão da medida da espera da malha (05/11/2026; implementar na semana 2)

**Contexto: quando surgiu.** Ao explicar o `Mesh ready after 10.0s`,
percebemos que a medida é grosseira:
- cada tentativa que falha custa ~2 s (até 1 s esperando o ping e mais 1 s de
  pausa);
- o valor real está entre ~8 e 10 s;
- o relógio só começa depois que os containers e o BATMAN já subiram. Essa
  parte anterior leva ~7 s e hoje não é medida.

**Por que importa.** O tempo de execução é uma das prioridades do projeto, e a
semana 2 vai gravar o tempo de cada fase. A espera da malha é uma dessas
fases.

**Dois tempos diferentes (não confundir).**

| | O que é | Muda? |
|---|---|---|
| Tempo limite (`network_ready_timeout_seconds`) | Quanto se aceita esperar antes de desistir | **Não** (continua 60 s) |
| Intervalo entre tentativas | De quanto em quanto tempo se testa | Sim, de ~2 s para ~0,2 s |

Testar com mais frequência não acelera nem atrasa o BATMAN, e não encerra a
execução mais cedo: só o tempo limite encerra. O testbed apenas percebe antes
que a malha ficou pronta. O ping continua esperando até 1 s pela resposta, para
que respostas lentas através da malha sejam aceitas.

**Efeito colateral.** Cerca de 5 pings por segundo durante a espera, em vez de
~0,5. São pacotes pequenos e terminam antes da captura e do protocolo.

**Decisão.** Opção (a), conferir a cada ~0,2 s, implementada junto com os
tempos por fase da semana 2. A alternativa recusada era manter como está e
anotar a precisão de ~2 s.
