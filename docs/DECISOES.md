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

**Correção (05/11, vista nos PCAPs da D12).** A limpeza não apaga tudo. O
BATMAN tem um cache de ARP próprio, a DAT (Distributed ARP Table), que o
`ip neigh flush` não limpa. No PCAP do sender, o ARP reply aparece 8 µs depois
do request (rápido demais para ter cruzado o rádio), e o ARP nem aparece no
PCAP do receiver: o próprio BATMAN do sender respondeu, com o que aprendeu
durante a espera. Consequência: a primeira mensagem do protocolo não paga o
custo de uma resolução ARP pela malha. O efeito é pequeno (frações de
milissegundo, só no primeiro pacote), mas a afirmação "o protocolo começa como
se os pings não tivessem acontecido" não é exata. Fica registrado; se for
relevante para algum experimento, a DAT pode ser desligada (`batctl dat 0`) ou
limpa.

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

**Por que importa.** Um sender que termina não garante que seus pacotes
chegaram: o último pode ainda estar atravessando a malha (mais ainda com um
drone intermediário). Sem o drain, o testbed desligaria o receiver com o
pacote a caminho, e isso apareceria como "perda" causada pelo próprio testbed.
Para a medida de entrega/perda da semana 3, o drain precisa ser maior que a
latência máxima esperada.

**Quanto pesa (execução real de 05/11).** A fase `execute_protocol` levou
2,5 s com os programas de exemplo (`bin/sender` envia um único pacote UDP de
1 byte e termina; `bin/receiver` só escuta até ser parado):

| Etapa | Tempo |
|---|---|
| Espera após iniciar o receiver (`receiver_startup_seconds`) | 1,0 s |
| Sender envia e termina | 0,03 s |
| Drain (`drain_seconds`) | 1,0 s |
| Receiver de exemplo percebe o SIGTERM (confere a cada 0,5 s) | ~0,5 s |

Ou seja, ~2 s são esperas fixas do testbed, e não trabalho do protocolo.

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

**Correção (05/11, ao implementar).** A precisão melhora menos do que foi dito
na discussão ("décimos de segundo"). A pausa entre tentativas caiu de 1 s para
0,2 s, mas enquanto a malha não está pronta cada ping ainda espera até 1 s pela
resposta. Cada tentativa que falha custa, então, ~1,2 s, e a precisão passa de
~2 s para **~1,2 s**, não para décimos. Para chegar a décimos seria preciso
também encurtar a espera de cada ping (ex.: `-W 0.3`): uma resposta mais
lenta que isso contaria como tentativa falha, mas isso só atrasaria a
detecção, nunca encerraria a execução. Fica como decisão em aberto.

**Onde.** `_READY_POLL_SECONDS = 0.2` em `scripts/testbed/network.py`.

---

## D9. Uma pasta por execução; campos desconhecidos no config viram erro (05/11/2026)

**Contexto: quando surgiu.** Primeiro item da semana 2. Até aqui, o bloco
`output:` do config fixava os nomes dos arquivos (`logs/exp-01-unicast.pcap`
etc.). Isso tinha três problemas:
- toda execução sobrescrevia a anterior;
- em broadcast os arquivos continuavam com "unicast" no nome;
- não ficava registrado com qual config cada resultado foi gerado.

Na mesma discussão apareceu um segundo problema: o testbed ignorava em
silêncio qualquer campo que não conhecia. Foi o que aconteceu com o `range`
(D7). Um erro de digitação como `postion:` também passaria despercebido, e o
nó rodaria na posição errada sem ninguém saber.

**Decisão (parte 1: resultados).**
- Cada execução grava em `<pasta base>/<experiment.id>/<AAAA-MM-DD_HH-MM-SS>/`,
  com nomes sempre iguais: `config.yml` (cópia exata do config usado),
  `capture.pcap`, `metrics.csv` e `summary.json`. Duas execuções no mesmo
  segundo recebem o sufixo `-2`, `-3`...
- A pasta base é escolhida na hora de rodar, com
  `scripts/run.py --output-dir <pasta>` (padrão `logs/`), e **não fica no
  config**. O config descreve o experimento; onde guardar é uma questão da
  máquina. Assim o mesmo config roda em outro computador sem edição, e ajuda a
  contornar o `logs/` gravado como `nfsnobody`.
- O bloco `output:` foi removido do config.
- Por que nomes fixos e não configuráveis: resultados comparáveis e fáceis de
  processar por script (ex.: ler todos os `logs/exp-01-unicast/*/summary.json`).
  É o padrão de ferramentas como Hydra e MLflow.

**Decisão (parte 2: validação estrita).**
- Qualquer campo desconhecido no config é erro antes de rodar, por exemplo:
  `configuration error: nodes[0]: unknown field 'postion'`.
- Isso substitui o aviso especial do `range` criado na D7. Não há mais avisos
  de compatibilidade.
- Por que não avisos: eles servem quando há usuários externos com configs
  próprios. O testbed ainda não foi lançado e o único config é o nosso. Quando
  houver usuários externos, remover campos com um período de aviso volta a
  fazer sentido.

**Onde.** `scripts/testbed/rundir.py` (cria a pasta e copia o config),
`scripts/testbed/config.py` (campos permitidos por seção), `scripts/run.py`
(fase `prepare_output` e `--output-dir`), `scripts/validate_scenario.py`
(localiza a pasta criada pela execução).

**Implicação.** Uma execução que falha antes do fim deixa a pasta só com o
`config.yml` (e o que tiver sido gerado até a falha). Isso é intencional: fica
o registro de que a tentativa existiu e com qual config.

---

## D10. Tempos do experimento, das fases e dos processos (05/11/2026)

**Contexto: quando surgiu.** Segundo item da semana 2. O tempo de execução é
uma das prioridades do projeto. Até aqui só existiam o `Mesh ready after` na
tela e a duração de cada processo no `summary.json`. Não se sabia quanto
levavam a montagem da rede, a limpeza ou a execução inteira.

**Decisão.**
- Cada execução grava um `timing.json` na sua pasta, com o início, o fim e a
  duração do **experimento inteiro** e de **cada fase**: `load_config`,
  `prepare_output`, `prepare_host`, `build_network`, `wait_network`,
  `start_capture`, `execute_protocol`, `stop_capture`, `write_outputs` e
  `cleanup`.
- Cada fase tem `status` `ok` ou `failed`. Numa execução que falha, a fase que
  falhou fica marcada e as seguintes (exceto `cleanup`) não aparecem.
- Cada processo (sender e receivers) ganhou `started_at` e `ended_at` no
  `summary.json`, além da `duration_seconds` que já existia.
- A tela mostra `Total time: X s` no fim.

**Por que dois relógios.**
- **Durações** usam um relógio monotônico, que só avança. Se o relógio do
  sistema for ajustado (NTP) durante a execução, as durações não são afetadas.
- **Instantes** (`started_at`, `ended_at`) usam o relógio do sistema, em
  segundos desde 01/01/1970 (Unix epoch). É a mesma base dos timestamps do
  PCAP, o que vai permitir alinhar fases, processos e pacotes na semana 3.
  Para leitura humana há também `started_at_local` e `ended_at_local`, no
  formato `2026-11-05T15:30:12.345-03:00`.

**Por que um arquivo separado (`timing.json`) e não dentro do `summary.json`.**
- O `summary.json` é escrito na fase `write_outputs`, antes da limpeza. O
  `timing.json` é escrito por último, então inclui a limpeza e o total real.
- Ele é gravado mesmo quando a execução falha no meio (ex.: `network not
  ready`), caso em que não existe `summary.json`. Fica registrado onde e
  depois de quanto tempo a execução falhou.
- A exceção é um erro no config: nesse caso a pasta da execução nem chega a
  ser criada.

**Confirmação real (05/11).** Total 28,4 s, igual à soma das fases:
`prepare_host` 3,4 s, `build_network` 7,3 s, `wait_network` 10,2 s,
`execute_protocol` 2,5 s e `cleanup` 5,0 s (as demais, milissegundos). O
protocolo ocupa ~9% do tempo; o resto é preparar e desmontar o cenário. O
pacote capturado no PCAP tem horário entre o `started_at` e o `ended_at` do
sender, o que confirma que testbed e PCAP usam a mesma base de tempo.

**Onde.** `scripts/testbed/timing.py` (classe `Timeline`), `scripts/run.py`
(uma chamada `timeline.start(...)` por fase; `timing.json` gravado no
`finally`), `scripts/testbed/runner.py` (instantes de cada processo).

---

## D11. Metadados de cada execução (05/11/2026)

**Contexto: quando surgiu.** Terceiro item da semana 2. O `config.yml`
copiado em cada pasta registra o cenário, mas não as versões do que rodou.
Exemplo do problema: em 05/11 um protocolo tem 2% de perda a 50 m e em 20/11,
com o mesmo config, 10%. Sem saber o que mudou (protocolo, testbed, imagem,
sistema), o resultado não pode ser explicado nem reproduzido.

**Decisão.** Cada execução grava `metadata.json` na sua pasta, logo após
criá-la (fase `collect_metadata`), com:

| Campo | Responde | Por que pode mudar sem perceber |
|---|---|---|
| `testbed.commit`, `branch`, `uncommitted_changes`, `changed_files` | Qual código do testbed rodou | O testbed muda toda semana; alterações não commitadas ficam marcadas |
| `binaries.<papel>.sha256` | Qual versão do protocolo rodou | O usuário troca o programa e o nome continua igual |
| `images.<nome>.id` | Qual imagem exata foi usada | Refazer `drone:latest` muda o conteúdo, não o nome |
| `host.kernel`, `host.batman_adv` | Qual roteamento estava ativo | Atualização do sistema muda o BATMAN |
| `host.packages`, `python`, `hostname`, `command`, `user` | Em que ambiente rodou | Outra máquina ou instalação |
| `randomness` | Por que os resultados variam | Ver abaixo |

**Aleatoriedade (limitação registrada no próprio arquivo).** O wmediumd sorteia
o fading e o testbed não consegue fixar essa semente. Mesmo config e mesmas
versões garantem o mesmo cenário, **não resultados idênticos**. Por isso o
plano prevê repetições com estatística.

**Detalhes de implementação.**
- O `run.py` roda com `sudo`, e o repositório pertence à usuária. Sem
  `git -c safe.directory=...`, o git recusa ler o repositório ("dubious
  ownership").
- `uncommitted_changes` ignora arquivos não versionados (ex.:
  `docs/Plano_execucao`), que não afetam o código executado.
- Cada item é coletado de forma isolada: se uma ferramenta faltar, o campo
  registra `{"error": ...}` e o experimento segue. Metadados nunca fazem uma
  execução falhar.

**Onde.** `scripts/testbed/metadata.py`; fase `collect_metadata` em
`scripts/run.py`.

**Confirmação real (05/11).** Commit `782a49e` lido mesmo com `sudo`; sha256
dos binários iguais aos do `sha256sum`; BATMAN-adv 2025.5; kernel
7.0.0-42-generic. O primeiro uso já revelou algo: a imagem `drone:latest` foi
criada em 23/09 às 17:35, cerca de meia hora **antes** da última mudança do
Dockerfile (commit `7c9625e`, 18:03). A imagem em uso pode não corresponder ao
Dockerfile atual.

---

## D12. Um PCAP por participante; filtro de captura opcional (05/11/2026)

**Contexto: quando surgiu.** Último item da semana 2, já previsto no plano.
Até aqui havia **uma** captura, só no sender, e com o filtro fixo
`udp port <porta>`. Com isso:
- não dava para saber o que chegou a cada receiver. Isso é necessário para a
  latência e a perda da semana 3, que comparam o PCAP do sender com o de cada
  receiver;
- qualquer tráfego fora da porta do protocolo era descartado na captura (ARP,
  ICMP, outras portas que o protocolo do usuário use), e não poderia ser
  analisado depois.

**Decisão.**
- Uma captura por **participante**: o sender e cada receiver. Arquivos
  `capture-<id do nó>.pcap` na pasta da execução. Em unicast são 2 arquivos;
  em broadcast, 1 + o número de receivers.
- Por padrão a captura grava **todo o tráfego** da interface
  (`wireless.interface`, `bat0`). Filtrar no momento da captura perde dados de
  forma irreversível, e filtrar na análise não perde nada.
- Seção nova e opcional no config: `capture.filter`, um filtro do tcpdump
  (BPF), por exemplo `"udp port 5000"`. Vazio = tudo. Serve para reduzir o
  tamanho dos PCAPs em execuções longas.
- As métricas atuais (`metrics.csv`) continuam vindo do **PCAP do sender** e
  olhando só UDP na porta do protocolo. A análise por receiver é a semana 3.
- Se um tcpdump morre logo ao iniciar (ex.: filtro com erro de sintaxe), a
  execução falha **antes** do protocolo, com a mensagem do tcpdump. Antes, isso
  só aparecia no fim, como captura vazia.

**O que a `bat0` mostra.** A `bat0` é a interface "virtual" do BATMAN: nela
aparece o tráfego IP/ARP dos programas (o que o protocolo envia e recebe). As
mensagens de roteamento do próprio BATMAN (OGMs) passam na `wlan0` e **não**
aparecem nesses PCAPs.

**Fora do escopo agora.** Capturar também nós que não são participantes (ex.:
um relay). Útil para ver o multi-hop por dentro; fica para as semanas 4–5 se
for necessário.

**Confirmação real (05/11).** Dois PCAPs na pasta. No do sender
(`capture-drone1.pcap`): ARP request, ARP reply e o pacote UDP para a porta
5000. No do receiver (`capture-gcs.pcap`): o mesmo pacote UDP chegando
1,73 ms depois. É a primeira evidência direta de entrega que o testbed
registra, e uma prévia manual da latência da semana 3.

**Onde.** `CaptureSet`, `capture_participants` e `start_capture` em
`scripts/testbed/metrics.py`; caminhos em `scripts/testbed/rundir.py`;
`capture.filter` em `scripts/testbed/config.py`.

---

## D13. Como casar pacotes entre PCAPs (08/11/2026; EM DISCUSSÃO, nada implementado)

**Contexto: quando surgiu.** Início da semana 3 (latência). Para medir latência
e perda é preciso reconhecer, no PCAP do receiver, **o mesmo pacote** que saiu
no PCAP do sender. O plano dizia "hash do payload com tshark". A usuária
perguntou por que hash, se demora e se há alternativas.

**Explicações dadas (08/11).**
- **Testes automáticos × execução real.** Os 187 testes do `unittest` não usam
  `tcpdump` real: ele é substituído por um *mock*, porque o sandbox não tem
  tcpdump/docker/mn, a captura exige `sudo` e rede montada, e os testes
  verificam a lógica do testbed (inclusive falhas difíceis de provocar). A
  ferramenta real é provada nas execuções no host (`sudo python3 scripts/run.py`).
- **Por que é preciso identificar o pacote.** Casar "1º com 1º" erra quando há
  perda: sender envia A, B, C; B se perde; o casamento por ordem liga B a C
  (latência inflada, perda atribuída ao pacote errado).
- **Custo.** O casamento roda depois do experimento, lendo arquivos; não
  altera a latência medida (instantes gravados pelo kernel na captura). Hash
  custa microssegundos por pacote; abrir o `tshark` custa mais (fração de s).
- **Portas e UDP.** IP = prédio, porta = apartamento. Porta de destino 5000 =
  `protocol.port`; porta de origem (ex.: 56770) é escolhida pelo sistema. Só
  UDP serve para casamento 1 a 1 (1 envio = 1 pacote); TCP reenvia/junta/divide;
  ARP e ICMP não são tráfego do protocolo.

**Alternativas analisadas.**

| Opção | Problema |
|---|---|
| Casar por ordem | Erra com perda |
| Hash do payload (plano original) | Payloads repetidos têm o mesmo hash (o `bin/sender` envia sempre `\x00`) |
| Comparar o payload direto | Igual ao hash |
| Testbed numerar pacotes | Proibido: altera o tráfego do protocolo |
| **ID do cabeçalho IP** (+ IPs, portas, tamanho) | Volta a zero após 65.536 pacotes; Linux pode usar ID 0 em alguns casos (a confirmar) |

**Evidência real (PCAPs de 05/11, `logs/exp-01-unicast/2026-11-05_15-39-36/`,
decodificados com Python):** o pacote UDP `192.168.123.2:56770 → .1:5000` tem
`IP-ID=27575` (DF ligado) **igual** no PCAP do sender e no do gcs: o BATMAN
transporta o pacote IP sem alterar o cabeçalho.

**Recomendação atual (aguardando ok da usuária).**
- Identificar pelo ID do IP (+ IPs, portas, tamanho); hash do payload só como
  conferência opcional. Resolve payloads repetidos.
- Possível consequência: ler PCAPs em Python puro e dispensar o `tshark`
  (fragmentos de um mesmo pacote compartilham o ID). Decisão da usuária.
- Em aberto também: só a porta do config × qualquer porta UDP saindo do IP do
  sender; nomes dos arquivos (`latency-<receiver>.csv`, bloco `latency` no
  `summary.json`); aviso quando a latência máxima se aproxima do
  `drain_seconds`.
- A confirmar no host: IP-ID não nulo e crescente com vários pacotes.

**Explicações dadas (09/11).**
- **PCAP sintético é só para os testes automáticos**, nunca para a medida.
  A latência sempre sai dos PCAPs reais do host. O sintético serve para
  provar a lógica do casamento com casos difíceis de provocar na rede real
  (perda, duplicata, ID que volta a zero, pacote fragmentado), sem docker e
  sem sudo, no sandbox.
- **tshark × Python é uma escolha independente do identificador.** O tshark
  também extrai o ID do IP (`ip.id`). A usuária quer usar o tshark.
  Consequência: o sandbox não tem tshark, então os testes que o chamam só
  rodam no host (no sandbox, são pulados ou usam a saída do tshark gravada).
- **Outras formas de medir latência** (além de casar pacotes em PCAPs):
  RTT com ping (mede a rede, não o protocolo); timestamps gravados pelo
  próprio programa do usuário (exige a colaboração dele); ferramentas de
  medição ativa como iperf3/OWAMP (medem a rede, não o protocolo);
  rastreamento no kernel com eBPF (send→recv do socket, inclui a pilha;
  complexo). Também importa **onde** se captura: em `bat0` a medida é
  rede-a-rede (não inclui o tempo dentro do programa).

**Decidido (09/11): o testbed mede latência de rede e perda; não mede
latência de programa a programa.**
- Por quê: os meios práticos de medir de programa a programa exigem que o
  programa grave horários e que o testbed leia a saída dele, o que fere o
  princípio de não interferir nem interpretar o protocolo. O eBPF só observa
  (não fere o princípio), mas é complexo demais para o prazo.
- A perda **não substitui** a latência de programa a programa: ela já vinha
  de graça do mesmo casamento (pacote enviado sem par no receiver = perdido)
  e já estava no plano. As saídas da semana 3 são as duas: latência de rede e
  entrega/perda, por receiver.
- Limite a documentar: a perda é medida na interface (`bat0`). Um pacote que
  chega à interface mas é descartado antes de o programa ler (ex.: buffer do
  socket cheio) conta como **entregue**.
- Resumo para o orientador: `docs/LATENCIA_REDE_X_PROGRAMA.md` (09/11).

**Ainda aguardando ok da usuária (09/11).** Substitui a "Recomendação
atual" acima onde conflitar (o Python puro saiu: a usuária prefere o tshark).

| # | Decisão | Proposta |
|---|---|---|
| 1 | Identificar o pacote | ID do IP + IPs + portas + tamanho; casa com o primeiro pacote ainda sem par que chegou depois do envio (resolve o ID que volta a zero) |
| 2 | Ferramenta | `tshark` (pré-requisito novo no host); testes que o chamam são pulados no sandbox |
| 3 | Tráfego analisado | ~~Só UDP do sender para `protocol.port`~~ → revisado abaixo: todo pacote IP entre participantes, nos dois sentidos |
| 4 | Saídas | `latency-<receiver>.csv` (um pacote por linha) + bloco `latency` no `summary.json` (média, mediana, p95, mín, máx, entregues, perdidos); aviso se a latência máxima se aproximar do `drain_seconds` |
| 5 | Sender de exemplo | Enviar 20 pacotes com 50 ms entre eles (hoje envia 1), para ver perda e confirmar no host que o ID muda; argumentos não mudam |

**Correção do entendimento (09/11).** A usuária não pediu o `tshark` na
análise: "usar o tshark" era só para **visualizar** os PCAPs. Fica assim:
- a captura continua com `tcpdump` (como hoje);
- a análise lê o PCAP em **Python puro** (nenhum pré-requisito novo no host);
- `tshark`/Wireshark são sugestão ao usuário para inspecionar os `.pcap`,
  não dependência do testbed.
A decisão 2 da tabela acima passa a ser "Python puro".

**Em discussão (09/11): validar com execuções reais em vez de PCAPs
sintéticos.** A usuária perdeu a noção do que é real e do que é teste e
propôs validar tudo com execuções reais que ela roda no host. Ponto a
decidir: o que fazer com testes automáticos (os 187 atuais e os novos).

**Revisão do item 3 (09/11, proposta).** A usuária lembrou que a D12 decidiu
**não** filtrar só UDP (o PQ-EDHOC usa CoAP, e o protocolo pode usar outros
tráfegos). Esclarecimento:
- A D12 trata da **captura**: continua gravando tudo. Isso não mudou.
- O "só UDP" do item 3 tratava da **análise de latência** e veio do plano
  original (`CLAUDE.md`: "hash do payload com tshark, só UDP"). Era
  necessário para casar por payload: no TCP um envio do programa não vira um
  pacote. Não foi uma decisão nova; foi herdado sem reavaliar. Erro do
  assistente.
- Com o casamento pelo **ID do IP**, a restrição deixa de ser necessária:
  qualquer pacote IP (UDP, TCP, ICMP) tem ID e pode ser casado entre as
  capturas. O que se mede é a latência e a perda **de cada pacote IP na
  rede**, não de cada mensagem do programa (no TCP, uma retransmissão é outro
  pacote; o pacote original perdido conta como perdido, mesmo que o TCP
  recupere o dado).
- Ponto novo: o EDHOC é um diálogo (mensagens nos dois sentidos). A análise
  deve casar pacotes **nos dois sentidos** entre cada par de participantes,
  não só sender → receiver.
- Proposta nova para o item 3: analisar todo pacote IP trocado entre
  participantes, nos dois sentidos, com resultados separados por sentido e
  por fluxo (protocolo + portas). ARP não é IP e fica fora (é do BATMAN/kernel).

**Quais métricas (09/11, proposta).** A usuária perguntou se média, mediana
e p95 são as ideais. Resposta do assistente:
- Média sozinha engana: um pacote lento (ex.: troca de rota) puxa a média.
  Mediana = caso típico; p95 = "quase pior caso"; máximo = pior caso.
- p95 só tem sentido com amostra suficiente: com 20 pacotes é praticamente o
  2º maior valor. Regra proposta: sempre mostrar `n`; p95 só com n ≥ 20 e
  p99 só com n ≥ 100 (senão `null`).
- Faltavam métricas importantes para drones/malha:
  - **jitter** (variação da latência entre pacotes consecutivos, média do
    |diferença|, como IPDV da RFC 3393): importa para controle e tempo real;
  - **rajada de perda** (maior sequência de pacotes perdidos seguidos): 10
    perdas espalhadas ≠ 10 seguidas; para um handshake, uma rajada derruba a
    sessão;
  - **fora de ordem** (pacote que chega antes de um enviado antes dele):
    acontece quando a rota muda (mobilidade/multi-hop);
  - **duplicados**.
- Desvio padrão: útil para repetições (semana 6), menos que o jitter aqui.
- Tempo do handshake completo (ex.: EDHOC) seria a métrica mais natural para
  o protocolo, mas exige saber o que é um handshake, ou seja, interpretar o
  protocolo: fora do escopo (D1). O CSV por pacote permite ao usuário
  calcular isso por conta própria.
- Latência ao longo do tempo (por janela) fica para as semanas 4–5, cruzada
  com as posições.

**Ainda é preciso casar pacotes? (09/11).** Sim. Todas as métricas novas
dependem de saber qual pacote do receiver é qual do sender: latência e
jitter (diferença de horário do mesmo pacote), rajada de perda (quais se
perderam), fora de ordem (a ordem de envio de cada um) e duplicados (o mesmo
pacote 2 vezes). Sem casamento só sobra contar (enviados − recebidos), que
dá a taxa de perda aproximada e erra quando há duplicados.

**Etapa 0 (09/11): sender de exemplo com 20 pacotes.** `bin/sender` envia
`PACKET_COUNT = 20` datagramas de 1 byte (`\x00`, sempre igual, de propósito:
payload idêntico é o caso que o hash não resolve), com
`PACKET_INTERVAL_SECONDS = 0,05` entre eles (~0,95 s no total, menor que o
`drain_seconds: 1`). Argumentos inalterados. TESTE AUTOMÁTICO: 188 OK (1 novo,
com socket falso). Falta a confirmação REAL: ID do IP não nulo e diferente em
cada um dos 20 pacotes, igual entre sender e receiver.

**Confirmação REAL da etapa 0 (09/11, `logs/exp-01-unicast/2026-11-09_14-39-09/`,
PCAPs decodificados com Python; `poc_ipid.log`).** `success`, malha pronta em
8,1 s, total 28,5 s.
- PCAP do sender: ARP request + reply (9 µs, DAT) e **20** pacotes UDP
  `192.168.123.2:37347 → .1:5000`, 29 bytes, DF ligado.
- PCAP do gcs: os **20**, com os **mesmos IDs**, na mesma ordem. Nenhuma perda.
- IDs: 60210, 60214, 60250, … 60655. Nenhum zero, nenhum repetido.
- Latência calculada à mão (casando pelo ID): mediana 0,25 ms, média 0,35 ms,
  mín 0,15 ms, máx 1,18 ms (3 pacotes acima de 0,9 ms puxam a média: exemplo
  real de por que a média sozinha engana).

**Correção (09/11).** O ID **não** sobe de 1 em 1: os saltos foram 4, 36, 43,
… (média ~23). O Linux soma um incremento aleatório ao contador (proteção
contra ataques que adivinham o ID). Consequências:
- o ID volta a zero bem antes de 65.536 pacotes: neste ritmo, a cada ~2.800
  pacotes (~140 s a 20 pacotes/s), não "só após 65.536";
- o ID não serve para saber a ordem nem para contar pacotes "faltando" pelo
  salto; a ordem vem do horário de envio no PCAP do sender (já era o plano);
- risco: se um pacote se perde e, depois da volta, outro pacote com o mesmo
  ID (e mesmos IPs/portas/tamanho) chega, o casamento pode ligá-los, com uma
  latência enorme e falsa. Proposta: **janela máxima de casamento** (o pacote
  do receiver só vale se chegar até X s após o envio), bem menor que o tempo
  de volta do ID.

**Explicações sobre a janela (09/11).**
- "5 s" não é padrão de nenhuma norma: é um valor sugerido pelo assistente
  para o limite de espera do casamento.
- Por que não casar só pelo IP: o IP identifica a **máquina**, não o pacote.
  Na execução real os 20 pacotes tinham os mesmos IPs, portas e tamanho; só
  o ID e o horário mudavam. O IP faz parte da chave, mas sozinho não separa
  pacotes.
- A janela **não força entrega** (o testbed não reenvia nem segura nada). Ela
  existe para que uma perda real **não seja escondida** por um casamento
  errado (pacote perdido ligado a outro pacote com o mesmo ID, minutos
  depois). Alinhado com o objetivo da usuária: mostrar onde o protocolo
  começa a perder pacotes.

**Contar × casar (09/11, em discussão).** A usuária propôs só contar
(pacotes que saíram × que chegaram, por IP), já que cada usuário conhece os
pacotes do próprio protocolo. Explicação do assistente:
- Contar dá a taxa de perda (aproximada: duplicados e tráfego extra entram
  na conta). Não dá **latência** (prioridade do projeto, CLAUDE.md), jitter,
  rajada de perda nem fora de ordem: todos precisam saber qual pacote é qual.
- Casar pelo ID **não depende do protocolo**: o ID é posto pelo kernel em
  todo pacote IP, seja qual for o programa. O testbed não precisa conhecer os
  pacotes do usuário; só reconhece "o mesmo pacote IP" nas duas capturas.
- O usuário sabe o que **enviou**, mas não sabe o que **chegou** nem quando:
  isso só a captura dos dois lados mostra.
Opções levadas à usuária: (A) só contar; (B) casar pelo ID (latência + perda
+ demais métricas).

**Decidido (09/11): opção (B), casar pelo ID do IP.** Esclarecido também:
neste testbed, "latência fim-a-fim" = latência **de rede** de ponta a ponta:
da interface `bat0` do nó que envia à `bat0` do nó que recebe, passando por
todos os saltos (relays) no caminho. Não inclui o tempo dentro dos programas
(ver `docs/LATENCIA_REDE_X_PROGRAMA.md`). Janela máxima de casamento: 5 s,
constante no código por enquanto (`MATCH_WINDOW_SECONDS`), a tornar
configurável se a usuária pedir.

**Etapas 1 a 3 implementadas (09/11; aguardando confirmação REAL).**
- `scripts/testbed/pcap.py`: leitor de PCAP em Python puro (PCAP clássico
  µs/ns, Ethernet/VLAN, Linux SLL/SLL2, raw). Extrai horário, IPs,
  protocolo, portas, ID do IP e tamanho; agrupa fragmentos pelo ID
  (envio = 1º fragmento, chegada = último). Nunca lê o payload.
- `scripts/testbed/latency.py`: casamento por (IPs, protocolo, portas,
  tamanho, ID) com janela de 5 s; todo pacote IPv4 entre participantes, nos
  dois sentidos; broadcast/multicast conta para cada receiver. Métricas por
  sentido (`total`) e por fluxo (`flows`): sent, delivered, lost, loss_rate,
  max_loss_burst, out_of_order, duplicates, latency min/median/mean/p95
  (n ≥ 20)/p99 (n ≥ 100)/max em ms (arredondadas a 1 µs) e jitter_mean_ms.
  Contagem do que ficou de fora em `ignored` (não IPv4, malformado, fora dos
  participantes).
- Saídas: `latency-<origem>-<destino>.csv` (um pacote por linha) e bloco
  `latency` no `summary.json`; aviso se a latência máxima ≥ 50% do
  `drain_seconds`. Fase nova `analyze_network` no `timing.json`; resumo por
  sentido impresso no terminal.
- A análise **nunca muda o status** da execução: perda é resultado do
  cenário, não falha do testbed. PCAP ilegível vira aviso.
- Rodada (só leitura) sobre os PCAPs REAIS de 09/11: 20/20, mediana 0,251 ms,
  média 0,354 ms, máx 1,182 ms, p95 1,058 ms, jitter 0,160 ms — igual à conta
  manual.
- TESTE AUTOMÁTICO: 202 OK (13 novos em `tests/test_latency.py`, com PCAPs
  sintéticos, e 1 em `test_metrics.py`).

**Confirmação REAL — unicast (09/11, `logs/exp-01-unicast/2026-11-09_15-02-42/`,
`poc_latency.log`).** `success`, sem avisos, malha pronta em 9,0 s, total
28,8 s. Terminal: `drone1->gcs: 20/20 delivered, median latency 0.240 ms`.
- `latency-drone1-gcs.csv` com 20 linhas, todas `delivered`, IDs 59920 …
  60498 (saltos aleatórios, como antes).
- `summary.json`: mín 0,187, mediana 0,240, média 0,344, p95 0,873, máx
  1,166 ms; jitter 0,157 ms; p99 `null` (n < 100); `ignored`: 2 não IPv4
  (ARP) no sender.
- Conferência independente (decodificação crua dos PCAPs, casando só pelo
  ID): 20/20, mediana 0,240, máx 1,166 ms. Bate.
- Fase `analyze_network`: 0,001 s.
- Falta: broadcast (por receiver) e cenário com perda (receiver afastado).

**Confirmação REAL — broadcast (09/11, `logs/exp-02-broadcast/2026-11-09_15-06-00/`,
`poc_broadcast.log`, config temporário `poc_broadcast.yml`: receivers gcs e
drone2).** `success`, sem avisos, malha 8,7 s, total 28,2 s.
- Destino `192.168.123.255`; um CSV por receiver
  (`latency-drone1-gcs.csv`, `latency-drone1-drone2.csv`), 20/20 em cada,
  mesmos IDs nos três PCAPs.
- gcs: mediana 0,929 ms, máx 0,978, jitter 0,040. drone2: mediana 0,986,
  máx 1,029, jitter 0,045. Conferência independente bate.
- Observação (hipótese, não verificada): broadcast ~4× mais lento que
  unicast (0,24 ms) porém mais estável. Provável causa: no 802.11 o
  broadcast vai na taxa básica (mais lenta) e sem confirmação/reenvio.

**REAL — unicast a 50 m (09/11, `logs/exp-03-distancia/2026-11-09_15-08-58/`,
`poc_distancia.yml`: gcs 0 m, drone1 50 m, drone2 200 m).** `success`, malha
9,0 s, **20/20 entregues**, mas mediana 0,922 ms (×4 em relação aos 20 m:
0,240) e jitter 0,226. Latências: 3 pacotes ~0,25 ms, o resto ~0,9 ms.
Hipótese (não verificada): em unicast o Wi-Fi reenvia quadros perdidos e
reduz a taxa de transmissão com sinal fraco; a perda vira latência. Ainda
sem cenário real com perda. Próximo: broadcast a 50 m (sem reenvio no
Wi-Fi).

**Hipóteses documentadas (09/11):** `docs/HIPOTESES_2026-11-09.md`
(broadcast mais lento e estável; a 50 m a perda vira latência). Inclui uma
alternativa: o BATMAN-adv pode repetir broadcasts e esconder parte da perda
no teste de broadcast a 50 m. Verificação proposta, só observando:
`iw dev wlan0 station dump` e captura na `wlan0`.

**REAL — broadcast a 50 m (09/11, `logs/exp-04-distancia-broadcast/2026-11-09_15-12-13/`,
`poc_distancia_bc.yml`).** `success`, **20/20**, mediana 0,938 ms (igual ao
broadcast a 20 m), mas dois pacotes atrasados: 2,348 e 4,191 ms; jitter
0,549 ms (era 0,040 a 20 m). Hipótese (não verificada): a primeira cópia se
perdeu e chegou uma repetição feita pelo BATMAN-adv alguns ms depois; a
perda virou atraso também em broadcast. Ainda sem cenário real com perda.
Próximo: broadcast a 58 m.

**REAL — broadcast a 58 m (09/11, `logs/exp-05-distancia-58m/2026-11-09_15-18-20/`).**
`success`, **20/20**, mediana 0,937 ms, máx 1,613 ms, jitter 0,108. Mesmo
perto do limite medido antes (~60 m, -90 dBm para limiar -91), sem perda.
Hipótese: no wmediumd a passagem de "enlace bom" para "sem enlace" é
abrupta (faixa intermediária estreita); a aleatoriedade (fading) muda o
resultado entre execuções. Próximo: 62 m (média do sinal abaixo do
limiar); se a espera da malha falhar, a perda real fica para a mobilidade
(semanas 4–5), em que o nó se afasta durante a execução.

**REAL — broadcast a 62 m (09/11, `logs/exp-06-distancia-62m/2026-11-09_15-21-28/`).**
`success`, **20/20**, mediana 0,924, p95 1,819, máx 3,639 ms, jitter 0,399
(atrasos de novo, como a 50 m). **Correção:** o "alcance ~60 m" do CLAUDE.md
era otimista como limite: na PoC de 05/11 (`poc_200ms.log`) o enlace direto
com o gcs ainda respondia ping às vezes até ~67 m e só falhou quase sempre
de ~72 m em diante (os -90 dBm registrados são o último valor recebido, não
o sinal real). Próximo: 70 m.

**REAL — broadcast a 70 m (09/11, `logs/exp-07-distancia-70m/2026-11-09_15-23-48/`).**
`success`, **20/20**, mediana 0,940, máx 3,198 ms, jitter 0,277. Fim da busca
por posição fixa (combinado antes de rodar).

**Situação da semana 3 em 09/11.**
- Confirmado REAL: casamento, latência e entrega em unicast e broadcast
  (por receiver), CSV e `summary.json`, em 6 execuções (20 m a 70 m).
- **Não confirmado REAL: perda.** Em nenhuma posição fixa houve pacote
  perdido; a degradação apareceu como atraso (máx e jitter sobem). A lógica
  de perda (lost, rajada, taxa) só está coberta por TESTE AUTOMÁTICO.
  Validação real adiada para a mobilidade (semanas 4–5): nó começa perto e
  se afasta durante a execução.
- Hipótese para não haver perda até 70 m: reenvios do Wi-Fi (unicast) e
  repetições do BATMAN (broadcast), ver `docs/HIPOTESES_2026-11-09.md`; o
  limite real do enlace direto é maior do que o "~60 m" anotado antes.

---

## D14. Testes automáticos: o que valem e quando somem (09/11/2026)

**Contexto: quando surgiu.** Na discussão da semana 3 (D13), a proposta de
gerar PCAPs sintéticos fez a usuária perder a noção do que era real e do que
era teste. Ela propôs validar tudo com execuções reais e aceitou manter os
testes automáticos com duas garantias.

**Garantia 1: só a execução real valida.**
- Teste automático (no sandbox do assistente, com mocks e dados sintéticos)
  **não confirma** nada. Serve só para avisar se uma mudança quebrou algo que
  já funcionava.
- Todo relato do assistente separa explicitamente:
  - **REAL**: rodado pela usuária no host, com o resultado lido dos arquivos;
  - **TESTE AUTOMÁTICO**: rodado no sandbox, sintético ou com mock.
- Um item do plano só é marcado como concluído depois da confirmação
  **REAL** (regra que já valia: "cada fase fecha com execução real").
- Dados sintéticos nunca entram nas saídas do testbed nem em `logs/`.

**Garantia 2: os testes serão removidos ao fim do projeto, sem afetar o
testbed.**
- Verificado em 09/11: nenhum arquivo de `scripts/`, `bin/` ou `dockerfiles/`
  importa ou usa algo de `tests/`. Apagar a pasta `tests/` não muda nenhum
  comportamento do testbed.
- Regra daqui em diante: o código do testbed **nunca** depende de `tests/`
  (nada de importar helpers de teste, nem dados de teste usados em execução).
  PCAPs sintéticos, se houver, ficam dentro de `tests/`.
- Na remoção, além de apagar `tests/`, só é preciso atualizar os textos que
  mencionam testes (README, CLAUDE.md, RELATORIO_TECNICO). Isso é documentação,
  não funcionamento.

**Onde.** `tests/` (todos os testes). Regra registrada também no `CLAUDE.md`.

---

## D15. Mobilidade (09/11/2026; implementada, aguardando confirmação REAL)

**Contexto: quando surgiu.** Semana 4 do plano. Além do plano, há um motivo
novo: a perda de pacotes não apareceu em nenhuma posição fixa (D13, 20 a
70 m), então a validação real da perda depende de um nó que se afasta
durante a execução. A PoC de 05/11 (`scripts/poc_mobility.py`) já provou
que `setPosition()` muda o sinal no wmediumd e que o BATMAN troca de rota.

**Estado atual do código.** Posições só fixas (`nodes[].position`). O
experimento termina quando o sender termina (+ `drain_seconds`); estourar
`duration_seconds` é falha ("sender timed out"). O sender de exemplo dura
~1 s.

**Propostas levadas à usuária (aguardando ok).**
1. Config: `nodes[].mobility` opcional; sem ele = parado. `type: trajectory`
   com `waypoints` `[t, x, y, z]` (t em segundos desde o início do sender)
   ou `waypoints_csv`; interpolação linear; após o último ponto, parado. O
   primeiro waypoint deve ser t=0 e igual a `position` (onde o nó fica
   durante a espera da malha); diferente = erro.
2. Quem move: thread do testbed chamando `setPosition()` a cada 0,1 s.
3. t=0 = início do sender (depois dos receivers subirem). Nada se move
   durante a espera da malha.
4. Fim do experimento: `experiment.end: sender | duration`. `sender` = como
   hoje. `duration` = o testbed encerra o sender (SIGTERM) em
   `duration_seconds`; normal, registrado `ended_by: testbed` (já era
   decisão de design no CLAUDE.md).
5. Sender de exemplo: enviar a cada 50 ms até receber SIGTERM (fluxo
   contínuo, como telemetria), argumentos inalterados; o config de exemplo
   passa a usar `end: duration`.
6. Logs durante a execução, só observando (nenhum pacote enviado):
   `positions.csv` (instante, nó, x, y, z), `routes.csv` (`batctl o` de cada
   nó a cada 1 s: originador, próximo salto, TQ, last-seen) e `signal.csv`
   (`iw station dump`: vizinho, sinal dBm).
7. `batman_orig_interval_ms`: manter 1000 como padrão ou passar para 200
   (na PoC, 1000 não trocou de rota a tempo; 200 trocou).
8. Validação REAL: drone1 sai de 20 m e vai a 150 m do gcs em 30 s, drone2
   fora do caminho; esperado: perda a partir de ~70 m, visível no CSV de
   latência e cruzável com `positions.csv`.
Semana 5 (depois): latência/perda por janela de tempo cruzada com posições.

**Decidido (09/11):** a usuária aprovou os 8 pontos: `experiment.end`
(campo novo), sender de exemplo a cada 50 ms até SIGTERM, e opção (a) no
item 7 (padrão continua 1000 ms; exemplos de mobilidade usam 200).

**Implementado (09/11).**
- `config.py`: `experiment.end` (`sender` padrão | `duration`);
  `nodes[].mobility` (`static` | `trajectory` com `waypoints` ou
  `waypoints_csv`), validado: ≥ 2 pontos, 1º em t=0 e igual a `position`,
  tempos crescentes, campos desconhecidos = erro.
- `mobility.py`: thread move os nós com trajetória a cada 0,1 s
  (`setPosition`), só depois do início do sender; `positions.csv` com todos
  os nós a cada 0,1 s (`time` epoch e `t` desde o início do sender).
- `observer.py`: thread lê `batctl o` e `iw dev <wlan> station dump` de
  todos os nós a cada 1 s → `routes.csv` (destino, melhor rota, próximo
  salto, TQ, last-seen) e `signal.csv` (sinal, taxa, tx retries, tx failed,
  inactive). Só leitura de tabelas locais; nenhum pacote enviado. Também
  serve para verificar as hipóteses de `HIPOTESES_2026-11-09.md` (taxa e
  reenvios do Wi-Fi). Uma trava por nó evita `setPosition` e `cmd`
  simultâneos no mesmo nó.
- `runner.py`: `duration_seconds` passa a contar **a partir do início do
  sender** (antes contava desde o início dos receivers, ~1 s a mais). Com
  `end: duration`, o sender encerrado no limite é normal; com `end: sender`,
  continua falha. Campo `ended_by` (`self` | `testbed`) no sender e nos
  receivers.
- `bin/sender`: um pacote a cada 50 ms até SIGTERM/SIGINT. **Mudança além do
  combinado:** se um envio falha (ex.: sem rota quando o drone sai do
  alcance), o erro é contado e o envio continua; antes o sender saía com
  código 1. Sem isso, a perda no cenário de mobilidade viraria "sender
  falhou". No fim imprime "N sent, M send errors".
- `scripts/config.yml`: `end: duration`, `duration_seconds: 10` (o padrão
  antigo de 60 s faria cada execução durar 60 s com o sender contínuo).
- Falhas de mobilidade/observação viram aviso, nunca falha da execução.
- **Atenção:** os `poc_*.yml` antigos não têm `end`, então valem `end:
  sender` e 60 s; com o sender contínuo, rodá-los de novo terminaria em
  "sender timed out" após 60 s. Eles continuam válidos como registro das
  execuções de 09/11.
- TESTE AUTOMÁTICO: 217 OK (13 novos em `tests/test_mobility.py`, nós
  falsos).
- Config de validação: `poc_mobilidade.yml` (unicast, drone1 de 20 m a
  150 m do gcs em 30 s, `end: duration` 35 s, `orig_interval` 200, drone2 a
  200 m).

**Explicação: os 10 s do config padrão (09/11).** `duration_seconds: 10` com
`end: duration` = o sender de exemplo roda 10 s (≈ 200 pacotes, 1 a cada
50 ms) e então o testbed o encerra (SIGTERM). Depois vêm o `drain_seconds`
(1 s) e a parada dos receivers. A execução inteira continua mais longa
(montar a rede, espera da malha, limpeza: ~20 s). Por que 10: o sender
contínuo nunca termina sozinho, então algo precisa encerrá-lo; 60 s
deixaria cada execução de exemplo lenta; 200 pacotes bastam para p95 e p99
(n ≥ 100). É só o valor do exemplo, o usuário escolhe. Com `end: sender`,
o mesmo campo vira só um limite de segurança.

**Explicação: como saber se o protocolo duraria mais (09/11).** Depende do
tipo de protocolo, e quem escolhe é o usuário:
- Protocolo que **termina sozinho** (handshake, transferência): `end:
  sender`, com `duration_seconds` folgado (limite de segurança). A duração
  natural fica registrada (`started_at`/`ended_at`, `duration_seconds` do
  sender, `ended_by: self`). Se passar do limite, a execução falha com
  "sender timed out" e `ended_by: testbed`: o usuário fica sabendo que durou
  mais e aumenta o limite.
- Protocolo **contínuo** (telemetria, fluxo): não tem duração natural;
  `end: duration` define a janela de observação. `ended_by: testbed` é o
  esperado. Se o sender terminar antes (`ended_by: self`), isso também fica
  registrado.
Nos dois casos o testbed nunca esconde o corte: `ended_by` diz quem
terminou cada programa.

**Confirmação REAL — mobilidade e perda (09/11,
`logs/exp-08-mobilidade/2026-11-09_16-14-38/`, `poc_mobilidade.log`).**
`success`, sem avisos, malha 9,0 s, total 62,6 s. 365 atualizações de
posição (10/s), 39 amostras de rotas/sinal. Sender: 698 enviados, 0 erros
de envio, `ended_by: testbed` em 35,07 s. Receiver `ended_by: testbed`.
- **Perda real pela primeira vez:** 407/698 entregues (41,7% de perda),
  maior rajada 76 pacotes, p99 110,7 ms. Conferência independente
  (decodificação crua, casando por ID): 407. Bate.
- Por faixa de distância (posição do drone1 no envio):

| Distância | Entregues | Mediana | Máx |
|---|---|---|---|
| 20–70 m | 229/229 | ~0,23 ms | 4,4 ms |
| 70–80 m | 46/46 | 2,4 ms | 3,8 ms |
| 80–90 m | 37/46 | 2,7 ms | 111,6 ms |
| 90–100 m | 39/45 | 2,5 ms | 106,4 ms |
| 100–110 m | 25/46 | 60,6 ms | 111,5 ms |
| 110–120 m | 13/46 | 102,6 ms | 111,1 ms |
| 120–150 m | 14/137 | ~106 ms | 110,9 ms |
| 150 m (parado) | 2/101 | 107,7 ms | 110,1 ms |

- Primeiro pacote perdido com o drone a 84 m. Antes da perda, a latência
  subiu (×10 a partir de ~70 m); perto do fim, o que chega leva ~100 ms.
- `signal.csv` (drone1 → gcs): sinal -63 dBm a 20 m, -80 a 50 m, -88 a
  ~72 m e depois oscilando entre -83 e -90 (o valor é do último quadro
  recebido, não desce abaixo do limiar -91). Taxa fixa em 13 Mbit/s após o
  início. **`tx failed` começa a crescer a ~111 m (1 → 157).** `tx retries`
  ficou 0 o tempo todo: ou não houve reenvio, ou o mac80211_hwsim/wmediumd
  não preenche esse contador. **A hipótese 2 (reenvio do Wi-Fi vira atraso)
  não foi confirmada nem descartada por esse contador.**
- `routes.csv`: rota direta via gcs o tempo todo (drone2 a 200 m, sem
  relay); TQ de 255 caiu a 133 e o last-seen chegou a ~4 s no fim.
- **Correção do alcance (de novo):** com movimento, a entrega foi 100% até
  ~84 m e ainda houve pacotes isolados a 150 m. Não há corte abrupto:
  latência sobe primeiro, depois a perda cresce até quase 100%.
- Hipótese (não verificada) para os ~100 ms: fila no Wi-Fi/BATMAN enquanto
  cada quadro espera tentativas de envio num enlace quase morto.

**Confirmação REAL — config padrão novo (09/11,
`logs/exp-01-unicast/2026-11-09_16-23-21/`).** `end: duration`, 10 s:
`success`, sender 199 enviados em 10,02 s, `ended_by: testbed`, 199/199
entregues, total 39,0 s (antes ~28 s). p99 apareceu (n ≥ 100): 3,225 ms.
- **Observação nova:** mediana 0,925 ms, contra 0,240 ms no unicast a 20 m
  da manhã (mesmas posições). `signal.csv`: o Wi-Fi usou **7,2 Mbit/s**
  para o gcs (13,5 para o drone2); na mobilidade, a 20–70 m, usou 13 Mbit/s
  e a mediana foi ~0,23 ms. Hipótese (não verificada), que pode unir as
  hipóteses 1 e 2 de `HIPOTESES_2026-11-09.md`: a latência ~0,9 ms aparece
  quando o quadro sai numa taxa baixa (broadcast sempre na taxa básica;
  unicast quando o Wi-Fi escolhe uma taxa baixa), ~0,2 ms em taxas mais
  altas. O "tx bitrate" do `iw` é a última taxa usada, então a evidência é
  indireta.
- `routes.csv`: o TQ do BATMAN para o gcs ainda subia durante toda a
  execução (29 → 133): a espera da malha termina quando o ping passa, não
  quando o BATMAN converge. Sem efeito visível aqui (enlace direto), mas
  relevante para multi-hop.

---

## Revisão antes do push (09/11/2026, noite)

A pedido da usuária, conferência geral antes de `git push`.
- TESTE AUTOMÁTICO: 217 OK; todos os módulos compilam (host: Python 3.12.3).
- **Resultados REAIS recalculados de forma independente** (script que lê os
  bytes dos PCAPs, sem usar o código do testbed): as 10 direções das 9
  execuções de 09/11 batem com o `summary.json` em enviados, entregues,
  mediana, máximo e jitter.
- Configs das execuções conferidos (posições, modo, `end`, duração,
  `orig_interval`); movimento da mobilidade conferido (x = 85 m em t = 15 s,
  atualizações a cada 0,1 s).
- Código conferido contra o guia: espera da malha, limpeza, drain também
  com `end: duration`, `ended_by`, janela de 5 s, percentis, aviso de drain.
- GitHub (`git fetch`): PRs #20 e #21 na `main`; a branch está 7 commits à
  frente da cópia remota e nada atrás (push sem conflito).

**Correções feitas nos documentos.**
- Broadcast de 15:06: os dois receivers estavam a **20 m** do sender (gcs em
  x=0 e drone2 em x=40, sender em x=20), não "20 e 40 m"
  (`RELATORIO_2026-11-09.md`, `HIPOTESES_2026-11-09.md`).
- Contagem de testes novos: semana 3 = 14 (13 + 1), semana 4 = 15 (13 + 2).
- Guia: o exemplo de config é "baseado no" `poc_mobilidade.yml`; status
  descrito com precisão; casamento em TCP/dois sentidos marcado como não
  confirmado no real.

**Limitação encontrada na revisão.** O `status` (`success`/`inconclusive`)
ainda usa o critério da semana 1: existir UDP na porta `protocol.port` no
PCAP do sender (`analyze_pcap`). Um protocolo só TCP, ou que use outra porta,
sairia `inconclusive` mesmo com tráfego. A análise de latência/perda não é
afetada. Rever na semana 6 (contrato novo). O `validate_scenario.py` não foi
rodado no host depois das semanas 3–4.
