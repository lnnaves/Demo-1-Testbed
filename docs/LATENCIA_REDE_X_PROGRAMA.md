# Latência de rede × latência de programa a programa

**Decisão (09/11/2026):** o testbed mede a latência **de rede** e a perda de
pacotes. Ele não mede a latência **de programa a programa**.

## O que cada uma mede

| | Latência de rede | Latência de programa a programa |
|---|---|---|
| Início | Pacote passa pela interface do sender | Programa do sender pede o envio |
| Fim | Pacote passa pela interface do receiver | Programa do receiver lê a mensagem |
| Inclui | Rádio, roteamento BATMAN, enlaces intermediários | Isso + pilha de rede + processamento do programa (ex.: cifrar) |

## Como o testbed mede

Cada participante grava uma captura (PCAP) da própria interface de rede. O
mesmo pacote é encontrado nas capturas do sender e do receiver, e a
diferença entre os horários dá a latência. Pacote sem par no receiver conta
como perdido. Os containers compartilham o relógio, então os horários são
comparáveis.

## Por que não medir de programa a programa

O princípio central do testbed é ser **agnóstico de protocolo**: ele não
altera o programa testado nem interpreta o que ele envia ou imprime.

- **Programa grava os próprios horários:** exige mudar o programa e que o
  testbed leia a saída dele. Fere o princípio.
- **Rastreamento no kernel (eBPF):** só observa, então não fere o princípio,
  mas é complexo demais para o prazo do projeto.

## Limite que decorre disso

A perda também é medida na interface. Um pacote que chega ao receiver, mas
que o programa não consegue ler (ex.: buffer cheio por lentidão), conta como
**entregue**.
