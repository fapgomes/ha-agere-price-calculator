# Calendário de tarifas AGERE — Design

**Data:** 2026-08-28
**Estado:** Aprovado (aguarda revisão do spec)
**Antecede:** `2026-08-27-agere-reading-log-design.md` (fase 1, implementada)

## Objetivo

Substituir a tarifa única fixa em código por um **calendário de tarifas com datas
de vigor**, e ensinar o motor a dividir um período de faturação quando este
atravessa uma mudança de tarifa. O calendário é semeado nas opções da entrada e
editável na UI.

## Problema

O motor usa uma tarifa única (`Tariff` em `const.py`, valores de 2026). Faturas
anteriores a 2026-02-01 calculam com preços errados. Pior: o problema não é só
histórico — **acontece uma vez por ano no período ao vivo**. Em fevereiro de 2026
a integração teria aplicado a tarifa nova ao período todo e errado a fatura.

## Evidência

Vinte faturas reais da tarifa Doméstico (2025-01 a 2026-08), dezenove do mesmo
contador e uma de outro local de consumo. Toda a regra abaixo foi reconstruída
delas e verificada num protótipo descartável: **18 das 19 batem ao cêntimo**.

As faturas contêm dados pessoais (leituras, consumos e totais) e **não entram no
repositório**. Ficam num ficheiro local ignorado pelo git,
`docs/agere-invoices.local.md`, para a validação poder ser repetida durante a
implementação. O que se publica são os **valores tarifários**, que são preços
públicos da AGERE.

### O calendário reconstruído

| `effective_from` | Componentes que mudam |
|---|---|
| `2024-12-12` | base completa (data mais antiga com prova) |
| `2025-01-01` | `tax_water 0,0382`, `tax_sanitation 0,0150` |
| `2026-01-01` | `tax_waste_mgmt 2,8821` |
| `2026-02-01` | escalões de água, `water_availability`, `sanitation_drainage`, `sanitation_availability`, `waste_variable`, `waste_fixed` |

Valores da base (`2024-12-12`): escalões `0,4751 / 0,6206 / 0,8048 / 1,7550 /
(desconhecido)`; `water_availability 4,5476`; `sanitation_drainage 0,4402`;
`sanitation_availability 4,4635`; `waste_variable 0,0136`; `waste_fixed 2,3310`;
`tax_water 0,0379`; `tax_sanitation 0,0141`; `tax_waste_mgmt 2,4260`.

Valores de `2026-02-01`: escalões `0,5080 / 0,6636 / 0,8605 / 1,8765 / 2,6852`;
`water_availability 4,8623`; `sanitation_drainage 0,4809`;
`sanitation_availability 4,8766`; `waste_variable 0,0147`; `waste_fixed 2,5257`.

Cada entrada do calendário guarda o **conjunto completo** dos valores, não um
delta: a coluna acima lista apenas o que difere da entrada anterior. É a
representação que permite exprimir datas de vigor por componente com uma lista
só.

As datas de vigor **não coincidem com o ano civil** e **diferem entre
componentes**. Uma tabela indexada por ano estaria errada de origem.

### A divisão é por componente, não por período

Numa fatura cujo período atravessa 2025-01-01, a AGERE divide **apenas as linhas
de taxas** — porque só as taxas de recursos hídricos mudaram nessa data:

```
Tx de Rec. Hídricos Água   <dias em dezembro>   0,037900
Tx de Rec. Hídricos Água   <dias em janeiro>    0,038200
Consumo Água [0 - 5]       <período completo>   0,475100
```

A água mantém-se numa linha única sobre o período inteiro, porque o seu preço não
mudou a 2025-01-01. Isto exclui a abordagem óbvia de partir o período e chamar o
motor atual por cada sub-período: partir a água a 01/01 reprorrateava os limites
dos escalões e reiniciava-os, produzindo um total diferente do faturado.

### As três regras de divisão

Reconstruídas de duas faturas cujos períodos atravessam mudanças de tarifa, e
ilustradas aqui com um período sintético `2026-01-20 ~ 2026-02-15` (27 dias,
15 m³) que atravessa 2026-02-01:

1. **Repartição do consumo, proporcional aos dias.** Sub-período A = 12 dias
   (20/01–31/01), B = 15 dias (01/02–15/02). `15 × 12/27 = 6,67 → 7`, e o resto
   (8) vai para o último. Confirmado nas faturas reais pelas linhas de drenagem e
   de resíduos variáveis, cuja repartição coincide com a das linhas de água.
2. **Escalões reiniciam do zero em cada sub-período**, com limites prorrateados
   pelos dias **desse** sub-período. A: 7 m³ em 12 dias, limites 2/4/6/10, linhas
   `2+2+2+1`. B: 8 m³ em 15 dias, limites 3/5/8/13, linhas `3+2+3` — de volta ao
   primeiro escalão.
3. **Encargos fixos cobrados uma só vez, à tarifa em vigor no fim do período.**
   Nas faturas reais, a disponibilidade de água e a de águas residuais aparecem
   numa linha única já à tarifa nova, e a segunda vem rotulada com o mês final.
   Confirmado independentemente por uma fatura que atravessa a mudança de
   `tax_waste_mgmt` a 2026-01-01 e mostra uma linha única já a `2,8821`.

O arredondamento é **linha a linha ao cêntimo**, e os subtotais somam cêntimos já
arredondados: `7 × 0,0379 = 0,2653 → 0,27` e `5 × 0,0382 = 0,191 → 0,19` aparecem
como duas linhas de 0,27 e 0,19, não como uma de 0,46.

### Duas anomalias registadas, nenhuma modelada

- **Numa das faturas com período dividido**, a linha `Tarifa Fixa Resíduos` não é
  impressa e o `Total sem IVA` impresso é inferior ao que o `TOTAL` implica,
  exatamente pelo valor dessa tarifa fixa. O encargo **é** cobrado: reconstruindo
  as parcelas com ele incluído, chega-se ao `TOTAL` impresso, ao `FAC` e ao
  `Saldo Atual`. É um defeito de impressão da AGERE nos períodos divididos, não
  uma regra de negócio. O modelo natural reproduz o total; não há exceção a
  modelar.
- **A fatura do outro local de consumo** tem linhas de `Acerto Períodos
  Anteriores` com quantidades negativas em cada componente, que explicam
  exatamente a diferença face ao nosso cálculo. Um acerto retroativo é
  imprevisível a partir do log de leituras. Excluída dos testes, com o motivo
  registado.

### A proração dos limites de escalão fica sem resolução

Limites observados, contra as duas fórmulas candidatas:

| Dias | Faturado | `round(l×d/30)` | `ceil(l×d/30)` |
|---|---|---|---|
| 33 | 6, 11, 17 | ✓ | ✓ |
| 32 | 6, 11 | **5**, 11 ✗ | ✓ |
| 30 | 5, 10, 15, 25 | ✓ | ✓ |
| 29 | 5, 10, 15 | ✓ | ✓ |
| 28 | 5, 9, 14 | ✓ | 5, **10** ✗ |
| 18 | 3, 6 | ✓ | ✓ |

As fórmulas só divergem quando a parte fracionária cai entre 0 e 0,5, o que nas
20 faturas acontece duas vezes: limite 2 a 28 dias (`9,333`; três faturas
independentes usam 9 → arredondamento normal) e limite 1 a 32 dias (`5,333`; uma
fatura usa 6 → arredondamento para cima). **Contradição direta.** Testadas
também prorações por dias de mês civil em vez de /30, com ambas as variantes: não
há regra única que satisfaça as duas. Nenhuma das 20 faturas tem 31 dias, que
seria o caso decisivo.

**Decisão:** manter `ROUND_HALF_UP` (18 de 19 exatas). Trocar para `ceil`
acertaria uma fatura e estragaria três. A proração fica numa função única e
nomeada, para que uma revisão futura seja de uma linha, e a fatura de janeiro de
2026 entra na suite como teste de desvio conhecido (0,16 €) com a explicação.

## Decisões de desenho

1. **Módulo novo `tariffs.py`**, puro (sem Home Assistant), como o `readings.py`.
   O `Tariff` sai do `const.py`, que fica só com chaves e omissões.
2. **O calendário é semeado nas opções** e passa a ser a fonte de verdade, como o
   log de leituras. Editável na UI.
3. **Atualizações acrescentam só datas mais recentes.** Uma release nova nunca
   sobrepõe nem reintroduz o que o utilizador editou ou apagou.
4. **Nada é inventado.** Valores desconhecidos falham alto.
5. **Sem migração.** Nenhuma opção é removida ou renomeada.

## Modelo de dados

```python
@dataclass(frozen=True)
class Tariff:
    water_tier_bounds: tuple[int, ...]
    water_tier_prices: tuple[Decimal | None, ...]   # None = preço desconhecido
    water_availability: Decimal
    sanitation_drainage: Decimal
    sanitation_availability: Decimal
    waste_variable: Decimal
    waste_fixed: Decimal
    tax_water: Decimal
    tax_sanitation: Decimal
    tax_waste_mgmt: Decimal

@dataclass(frozen=True)
class TariffPeriod:
    effective_from: date
    tariff: Tariff

class TariffSchedule:
    def at(self, day: date) -> Tariff
    def change_dates_for(self, component: str, start: date, end: date) -> list[date]
    def merge_newer(self, builtin: TariffSchedule, seeded_through: date | None) -> TariffSchedule
```

`change_dates_for` é o mecanismo central: devolve as datas de mudança **em que o
valor desse componente muda de facto**. Para os escalões, o "componente" é o par
`(bounds, prices)`. É este filtro que faz janeiro de 2025 sair certo.

Nas opções, mesma convenção do log de leituras (valores em string, `null` para
desconhecido):

```json
"tariffs": [
  {"effective_from": "2026-02-01",
   "water_tier_bounds": [5, 10, 15, 25],
   "water_tier_prices": ["0.5080", "0.6636", "0.8605", "1.8765", "2.6852"],
   "water_availability": "4.8623", "sanitation_drainage": "0.4809",
   "sanitation_availability": "4.8766", "waste_variable": "0.0147",
   "waste_fixed": "2.5257", "tax_water": "0.0382",
   "tax_sanitation": "0.0150", "tax_waste_mgmt": "2.8821"}
],
"tariffs_seeded_through": "2026-02-01"
```

**Validação no construtor:** lista não vazia, datas únicas, valores não
negativos, `water_tier_bounds` estritamente crescente e de tamanho
`len(water_tier_prices) - 1`.

**Semeadura**, em `async_setup_entry` antes de carregar as plataformas: se
`tariffs` não existir, escreve o built-in e `tariffs_seeded_through` = data mais
recente. Se existir, acrescenta apenas built-ins com `effective_from` posterior à
marca e atualiza-a. Escreve as opções só quando há algo a acrescentar, para não
recarregar a entrada em cada arranque.

A marca existe para que **apagar uma entrada fique apagado**: comparar com a mais
recente *guardada* faria reaparecer o que o utilizador apagasse.

## Motor

Assinatura passa a datas, e `days` é derivado:

```python
def calcular(start: date, end: date, consumption: Decimal, config: CalcConfig) -> Breakdown
def marginal_price(start: date, end: date, consumption: Decimal, today: date, config: CalcConfig) -> Decimal
```

Isto elimina uma classe de bug do motor atual, em que `calcular(consumo, days, …)`
aceita um `days` que não corresponde às datas e ninguém verifica.

Cinco funções pequenas, cada uma testável isolada:

```python
_sub_periods(start, end, change_dates) -> list[SubPeriod]   # (start, end, days)
_allocate(consumption, subs) -> list[Decimal]               # ∝ dias, resto na última
_water_lines(subs, quantities, schedule) -> list[Line]      # escalões por sub-período
_rate_lines(component, subs, quantities, schedule) -> list[Line]
_fixed_line(component, end, schedule) -> Line
```

`_allocate` arredonda todas as parcelas menos a última e dá o resto à última.
Preserva o total e reproduz as duas faturas divididas; arredondar cada parcela
independentemente pode não somar (7 m³ em 15+15 dias daria 4+4=8).

Componentes **variáveis** (escalões, `sanitation_drainage`, `waste_variable`,
`tax_water`, `tax_sanitation`) dividem-se nas suas próprias datas. Componentes
**fixos** (`water_availability`, `sanitation_availability`, `waste_fixed`,
`tax_waste_mgmt`) são cobrados uma vez, à tarifa de `end`.

A taxa de IVA **não** entra no calendário e nunca é dividida: é uma opção do
utilizador (`vat_rate`), não um valor tarifário da AGERE, e está a 6% em todas as
20 faturas. Uma mudança de IVA a meio de um período fica fora de âmbito.

O `Breakdown` passa a ter linhas com a estrutura da fatura:

```python
@dataclass(frozen=True)
class Line:
    component: str        # "water_tier_2", "sanitation_drainage", "tax_waste_mgmt", …
    start: date | None    # None nos fixos, que cobrem o período todo
    end: date | None
    qty: Decimal
    rate: Decimal
    value: Decimal        # já arredondado ao cêntimo
    vat: bool             # a isenção do art. 2 nº2 CIVA vive aqui
```

Dois ganhos: os subtotais passam a ser somas das linhas, logo não podem divergir
delas; e a isenção de IVA fica codificada uma vez, na linha, em vez de estar
escrita em dois sítios como hoje.

## Preços desconhecidos

`water_tier_prices[4]` é `None` na base — o escalão >25 m³ nunca foi faturado
antes de 2026-02-01. Se um período o alcançar, levanta-se `UnknownTariffValue`
nomeando o escalão, o sub-período e onde preencher o valor.

Não se usa o preço do escalão anterior: o salto do 4º para o 5º é de +43% em 2026
(`1,8765 → 2,6852`), pelo que produziria um total plausível e errado por baixo.

O efeito é localizado: nos períodos fechados, só esse período aparece com erro na
lista de `sensor.agere_last_invoice`; os outros continuam calculados. No período
ao vivo não acontece na prática, porque a tarifa atual tem os cinco preços.

Pela mesma razão, a base começa a `2024-12-12` (data mais antiga com prova) e não
no início dos tempos: `at(day)` com `day` anterior levanta erro em vez de aplicar
preços de 2024 a uma fatura de 2023.

## UI

O menu de opções ganha uma quarta entrada:

```
init (menu)
 ├─ Readings
 ├─ Next reading date
 ├─ Tariffs            ← nova
 └─ Charges and VAT
```

**`tariffs`** — `SelectSelector` com as datas de vigor, da mais recente para a
mais antiga, anotadas com o que cada uma mudou face à anterior, mais uma opção
"nova data de vigor".

**`tariff_edit`** — data de vigor, valores e um toggle de apagar.

- **Campos de texto, não numéricos.** Os preços têm 6 decimais na fatura e um
  `NumberSelector` passa por `float`. Texto parseado para `Decimal` é exato, e é
  a convenção que o `vat_rate` já usa.
- **Limites dos escalões num campo só**, `"5,10,15,25"`, em vez de quatro.
- **Cópia para a frente:** ao editar, os valores da própria entrada; ao criar, os
  da entrada mais recente, porque uma tarifa nova sucede sempre à última.
- O preço do escalão >25 m³ pode ficar vazio, que representa "desconhecido".

**Validação:** data única, valores parseáveis e não negativos, limites
estritamente crescentes e em número igual aos preços menos um, calendário nunca
vazio. Erros com mensagem concreta via `description_placeholders`, como já se faz
nas leituras.

## Sensores e serviços

`CalcConfig` leva `schedule: TariffSchedule` em vez de `tariff: Tariff`.
`_calc_config(options)` constrói-o das opções, caindo no built-in se ainda não
existirem. Os ciclos fechados são calculados um a um com `try/except
UnknownTariffValue`, para o erro ficar contido nesse período.

Atributos novos em `sensor.agere_total_cost`:

```
tariff_effective_from: "2026-02-01"     # a tarifa aplicada aos encargos fixos
tariff_split: false                     # true quando o período atravessa uma mudança
sub_periods: []                         # quando dividido: datas, dias e m³ de cada
```

O atributo `tiers` é **substituído** por `lines` (quebra registada no CHANGELOG;
`lines` diz tudo o que `tiers` dizia e mais). `current_tier` mantém-se.

Em `sensor.agere_last_invoice`, cada entrada de `cycles` passa a ter `total` **ou**
`error`, nunca ambos.

A resposta de `agere_water.set_reading` mantém a forma e ganha `tariff_split`.

## Testes

As faturas reais **não entram no repositório** — contêm leituras, consumos e
totais, que são dados pessoais. Ficam em `docs/agere-invoices.local.md`, ignorado
pelo git, e a validação contra as 19 faturas é feita localmente durante a
implementação, com o resultado reportado (não commitado).

O que a suite leva:

- **Fixtures sintéticas** construídas para exercitar cada regra: períodos de 28,
  29, 30, 32 e 33 dias, períodos que atravessam cada uma das três datas de vigor,
  e consumos que alcançam cada escalão. Os totais esperados são calculados com o
  motor e fixados nos testes.
- **As duas faturas reais já publicadas** antes deste trabalho (28 m³/30 dias e
  18 m³/28 dias), que continuam a ser as âncoras contra faturação real.
- **O desvio de 32 dias** como teste documentado, com o valor do desvio mas sem o
  período nem o consumo que lhe deram origem.

**Os dois casos divididos ganham testes linha a linha**, não só de total — são os
únicos que exercitam a divisão, e um total pode acertar por compensação de erros.
Um caso que atravessa 2025-01-01 prova o filtro por componente (água numa linha,
taxas em duas); um que atravessa 2026-02-01 prova o reinício dos escalões e os
fixos à tarifa do fim.

**Testes unitários** das peças novas, todos puros: `_sub_periods`, `_allocate`
(incluindo 7 m³ em 15+15 dias), `TariffSchedule.at` (incluindo o erro antes de
2024-12-12), `change_dates_for` (o caso de janeiro de 2025), `merge_newer`
(acrescentar-só-as-mais-recentes e a marca), e `UnknownTariffValue`.

**Testes de UI** no options flow: menu com quatro entradas, criar com cópia para
a frente, editar, apagar, e cada regra de validação. Correm só em CI, como os da
fase 1.

## Fora de âmbito

- Estatísticas históricas externas (a fase 2 do spec anterior; continua com plano
  próprio).
- Tarifas que não a Doméstico. O calendário é editável, logo quem tenha outra
  tarifa pode introduzi-la, mas não se enviam built-ins para ela.
- Modelar `Acerto Períodos Anteriores`. Quando a AGERE emite um acerto
  retroativo, o nosso número difere pelo valor do acerto.
- Resolver a proração a 32 dias. Fica documentada e testada como desvio conhecido
  até haver uma fatura de 31 ou 32 dias que decida.
