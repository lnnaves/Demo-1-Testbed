# Binários de exemplo: `bin/sender` e `bin/receiver`

O produto deste repositório é a **plataforma de execução de protocolos** no
cenário emulado de drones. `bin/sender` e `bin/receiver` **não são um
protocolo**: servem para testar a infraestrutura e são os comandos usados
quando o config não tem o bloco `commands`.

**Para integrar o seu protocolo, leia
[`docs/GUIA_INTEGRACAO.md`](../docs/GUIA_INTEGRACAO.md).** Desde a semana 6
(decisão D18), o usuário escreve o comando do seu programa no config, com
marcadores (`{address}`, `{destination}`, `{port}`, `{node_id}`, `{mode}`,
`{data_dir}`, `{output_dir}`), em qualquer formato de argumentos. Não é mais
preciso trocar estes arquivos nem imitar a interface deles.

## Comandos padrão (sem `commands` no config)

```bash
/opt/protocol/bin/receiver --address {address} --port {port}
/opt/protocol/bin/sender   --destination {destination} --port {port}
```

O testbed monta `bin/` do repositório em `/opt/protocol/bin` (só leitura).
`{destination}` é o IP do receiver em unicast e o endereço de broadcast em
broadcast.

## O que fazem

- **sender:** envia 1 byte (`\x00`) por UDP a cada 50 ms até receber
  SIGTERM/SIGINT, como um fluxo de telemetria. Um envio que falha (ex.: sem
  rota) é contado e o envio continua. No fim imprime
  `N sent, M send errors`. Por nunca terminar sozinho, precisa de
  `experiment.end: duration`.
- **receiver:** escuta na porta até SIGTERM/SIGINT e imprime
  `Receiver finished`. Não responde nada.

O payload é sempre o mesmo de propósito: o testbed não pode depender do
conteúdo para distinguir pacotes (ele usa o ID do cabeçalho IP; D13).

## Códigos de saída

| Situação | Como o testbed trata |
|---|---|
| Encerrado pelo testbed (SIGTERM) | Normal com qualquer código; `ended_by: testbed` |
| Terminou sozinho com `0` | Normal; `ended_by: self` (não é sucesso semântico do protocolo) |
| Terminou sozinho com outro código | Falha operacional |
