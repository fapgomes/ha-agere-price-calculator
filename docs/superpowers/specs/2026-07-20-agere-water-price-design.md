# AGERE Water Price Calculator — Design

**Data:** 2026-07-20
**Estado:** Aprovado (aguarda revisão do spec)

## Objetivo

Custom integration para Home Assistant que calcula o custo da água da AGERE
(tarifa Doméstico) a partir do consumo em m³ de um medidor existente. O valor
não é linear: depende de escalões de consumo, encargos fixos e IVA. A integração
expõe sensores que alimentam o painel de Energia do HA e dashboards, com o custo
a bater certo com a fatura da AGERE.

## Contexto

- O utilizador tem um medidor de água em HA que reporta o consumo total em m³
  (entidade monotónica, ex.: `sensor.water_meter_rf_test_izar_total`).
- O painel de Energia do HA já suporta custo de água via:
  - *"Use an entity with current price"* → `custo += Δconsumo × preço`
  - *"Use an entity tracking the total costs"* → lê um total de custo acumulado
- A tarifa AGERE não é um preço fixo por m³, logo um único €/m³ não a representa.

## Estrutura tarifária (tarifa Doméstico, extraída das faturas de 2026)

### Água — escalões (€/m³, base 30 dias)
| Escalão | Preço |
|---|---|
| 0–5 m³ | 0,508000 |
| 5–10 | 0,663600 |
| 10–15 | 0,860500 |
| 15–25 | 1,876500 |
| >25 | 2,685200 |

- **Disponibilidade Água 20 mm** (fixo/período): 4,862300

### Saneamento
- Drenagem águas residuais (€/m³): 0,480900
- Disponibilidade águas residuais (fixo/período): 4,876600

### Resíduos urbanos (não sujeito a IVA — nº2 artº2 CIVA)
- Tarifa variável resíduos (€/m³): 0,014700
- Tarifa fixa resíduos (fixo/período): 2,525700

### Pagamentos ao Estado (Taxas)
- Taxa recursos hídricos — água (€/m³): 0,038200
- Taxa recursos hídricos — saneamento (€/m³): 0,015000
- Taxa gestão resíduos (fixo/período): 2,882100 *(não sujeito a IVA)*

### IVA
- **6%** aplicado a: água (escalões + disponibilidade), saneamento (drenagem +
  disponibilidade), taxas de recursos hídricos (água + saneamento).
- **Não sujeito a IVA**: tarifa variável resíduos, tarifa fixa resíduos, taxa
  gestão de resíduos.

### Proração dos escalões pelos dias do período
Os limites dos escalões são proporcionais aos dias do período de faturação:
`limite_efetivo = round(limite_base × dias / 30)`.

Confirmado nas faturas:
- 30 dias → limites 5 / 10 / 15 / 25 (sem alteração).
- 28 dias → `round(5×28/30)=5`, `round(10×28/30)=9`, `round(15×28/30)=14`,
  `round(25×28/30)=23` → escalões 5 / 9 / 14 / 23, o que reproduz os
  faturados 5 / 4 / 5 / 4 para 18 m³.

## Arquitetura

Custom component `agere_water` (estrutura instalável via HACS), com config flow
por UI. Recalcula os sensores quando a entidade do medidor muda de estado
(`async_track_state_change_event`). Persiste o estado do ciclo via `Store` para
sobreviver a reinícios do HA.

Divisão em unidades com responsabilidade única:

- **`calculator.py`** — motor de cálculo puro, sem dependências do HA. Função
  `calcular(consumo_m3, dias, config) -> Breakdown`. Testável isoladamente
  contra as faturas reais.
- **`cycle.py`** — gestão do ciclo de faturamento (baseline, dias decorridos,
  reset no dia configurado). Persistência via `Store`.
- **`sensor.py`** — entidades de sensor que compõem calculator + cycle e expõem
  estado + atributos.
- **`config_flow.py`** — setup inicial e opções (reload em alteração).
- **`const.py`** — domínio, chaves de config, defaults das tarifas.

## Modelo de ciclo (`cycle.py`)

- Reset no **dia do mês configurável** (1–28).
- No dia de reset, captura a leitura atual do medidor como *baseline* e regista a
  data de início do ciclo.
- `consumo_ciclo = total_atual − baseline`.
- `dias_decorridos = max(1, hoje − início_ciclo)`.
- Escalões prorrateados por `dias_decorridos`. No fecho do ciclo,
  `dias_decorridos` iguala os dias do período, convergindo para a fatura.

## Motor de cálculo (`calculator.py`)

Função pura que recebe consumo do ciclo, dias e configuração e devolve um
breakdown com:
- valor por escalão de água + disponibilidade água
- saneamento (variável + fixo)
- resíduos (variável + fixo)
- taxas (rec. hídricos água + saneamento + gestão resíduos)
- base sem IVA, valor do IVA (6% só sobre as componentes sujeitas), total

Apenas as componentes ativas na configuração entram no total. Encargos fixos
entram por inteiro desde o início do ciclo (não prorrateados), tal como a fatura
os cobra (1,0000 unidade/período).

## Sensores expostos (`sensor.py`)

| Sensor | Unidade | Descrição | Uso no Energy |
|---|---|---|---|
| `sensor.agere_preco_marginal` | EUR/m³ | preço do escalão atual (+ variáveis ativas) | *entity with current price* |
| `sensor.agere_custo_total` | EUR | custo acumulado no ciclo (componentes ativas + fixos) | *entity tracking total costs* (recomendado) |
| `sensor.agere_custo_agua` | EUR | sub-custo água | dashboards |
| `sensor.agere_custo_saneamento` | EUR | sub-custo saneamento | dashboards |
| `sensor.agere_custo_residuos` | EUR | sub-custo resíduos | dashboards |
| `sensor.agere_custo_taxas` | EUR | sub-custo taxas | dashboards |
| `sensor.agere_consumo_ciclo` | m³ | consumo no ciclo atual | dashboards |

- `custo_total` e sub-custos: `state_class = total_increasing` (reset por ciclo
  tratado pelo HA), `device_class = monetary`.
- `preco_marginal`: `state_class = measurement`.
- Sub-sensores por componente criados apenas quando a componente está ativa.

Atributos em `custo_total`: base sem IVA, valor do IVA, consumo do ciclo, dias
decorridos, escalão atual, detalhe por escalão.

## Configuração (config flow + opções)

- **Entidade do medidor** (total em m³).
- **Dia de reset do ciclo** (1–28).
- **Componentes ativas**: água / saneamento / resíduos / taxas (toggles
  independentes).
- **Incluir IVA** (toggle) + **taxa de IVA** (default 6%).
- **Diâmetro / tarifa de disponibilidade** (default 20 mm → 4,862300).
- **Valores das tarifas** (defaults dos valores acima; editáveis porque a AGERE
  atualiza anualmente).

## Decisões de desenho

- **Encargos fixos entram por inteiro** desde o início do ciclo (não
  prorrateados) — reflete a cobrança da fatura.
- **Energy → apontar ao `custo_total`** (rigoroso). O `preco_marginal` é uma
  aproximação incremental dos escalões e não capta os encargos fixos.
- **Proração por dias decorridos** durante o ciclo (estimativa em tempo real que
  converge para a fatura no fecho).

## Testes

Testes unitários do `calculator.py` validados contra as duas faturas reais:

| Cenário | Consumo | Dias | Água | Total c/ IVA |
|---|---|---|---|---|
| Fatura 0049220236 | 28 m³ | 30 | 41,85 € | 71,21 € |
| Fatura 0049259391 | 18 m³ | 28 | 21,86 € | 44,21 € |

Validar também: proração dos escalões (5/9/14/23 aos 28 dias), IVA só sobre
componentes sujeitas, resíduos fora do IVA, e o comportamento de reset do ciclo
em `cycle.py`.

## Fora de âmbito (YAGNI)

- Leitura automática de faturas / integração com o portal AGERE.
- Tarifas não-domésticas.
- Previsão de custo futuro / projeção de fim de ciclo.
- Histórico de faturas.
