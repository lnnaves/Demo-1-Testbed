# Hipóteses sobre a latência observada (09/11/2026)

Execuções reais com o sender de exemplo: 20 pacotes UDP de 1 byte, 50 ms
entre eles. A latência é **de rede**, de `bat0` a `bat0`. As duas
explicações abaixo são **hipóteses**: os dados são reais, a causa ainda não
foi verificada.

## Dados

| Execução | Distância | Entregues | Mediana | Jitter |
|---|---|---|---|---|
| Unicast | 20 m | 20/20 | 0,240 ms | 0,157 ms |
| Broadcast (2 receivers) | 20 m e 40 m | 20/20 cada | 0,929 / 0,986 ms | 0,040 / 0,045 ms |
| Unicast | 50 m | 20/20 | 0,922 ms | 0,226 ms |

## Hipótese 1: broadcast é ~4× mais lento, mas mais estável

**Observado.** Broadcast com mediana ~0,93 ms contra 0,24 ms do unicast,
mas com jitter ~4× menor.

**Explicação provável.** No Wi-Fi (802.11), quadros de broadcast:
- saem na **taxa básica** do rádio (a mais lenta), para que todos os
  vizinhos consigam receber; o quadro fica mais tempo no ar;
- não têm confirmação (ACK) nem reenvio; por isso o tempo varia pouco.

**Explicação alternativa.** O BATMAN-adv trata broadcast de forma própria
(empacota, numera e pode repetir o envio no rádio). Esse processamento
também pode somar tempo.

## Hipótese 2: a 50 m, a perda do rádio vira latência

**Observado.** A 50 m nenhum pacote se perdeu, mas a mediana subiu de 0,24
para 0,92 ms. Três pacotes ficaram em ~0,25 ms, como a 20 m, e os outros 17
em ~0,9 ms.

**Explicação provável.** Em unicast, o próprio Wi-Fi:
- **reenvia** o quadro que não recebeu confirmação;
- **reduz a taxa** de transmissão quando o sinal está fraco.

O pacote chega, só que mais tarde. Para o protocolo testado, o efeito
aparece como **latência maior**, não como perda.

## Consequência para o próximo teste

Em broadcast, o Wi-Fi não reenvia, então a perda deveria aparecer. Porém,
se o BATMAN-adv repetir os broadcasts (explicação alternativa da hipótese
1), ele também pode esconder parte da perda.

## Como verificar (só observando, sem alterar o rádio)

- `iw dev wlan0 station dump` antes e depois de cada execução: mostra a
  taxa usada (`tx bitrate`), os reenvios (`tx retries`) e as falhas
  (`tx failed`) por vizinho.
- Comparar com uma captura na `wlan0`, abaixo do BATMAN, para ver quantas
  vezes cada quadro foi para o ar.

## Atualização (tarde de 09/11): a taxa do Wi-Fi pode explicar as duas

Com o `signal.csv` (D15), que registra a taxa usada pelo Wi-Fi:

| Execução | Taxa para o gcs | Mediana |
|---|---|---|
| Mobilidade, 20–70 m | 13 Mbit/s | ~0,23 ms |
| Config padrão, 20 m | 7,2 Mbit/s | 0,925 ms |

Hipótese: ~0,9 ms quando o quadro sai numa taxa baixa (broadcast sempre na
taxa básica; unicast quando o Wi-Fi escolhe uma taxa baixa) e ~0,2 ms em
taxas mais altas. Evidência indireta: o `iw` mostra só a última taxa usada.
O contador `tx retries` ficou 0 em todas as execuções; não serve para
confirmar ou descartar reenvios.
