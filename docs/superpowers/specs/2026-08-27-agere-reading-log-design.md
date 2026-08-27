# Log de leituras AGERE — Design

**Data:** 2026-08-27
**Estado:** Aprovado (aguarda revisão do spec)

## Objetivo

Substituir o modelo de ciclo de faturação por dia fixo do mês (`reset_day`) por
um **log de leituras do contador**, de modo a que os períodos de faturação
coincidam exactamente com os da AGERE. Permitir introduzir e editar leituras
pelo frontend (incluindo de meses passados) e, numa segunda fase, reconstruir o
histórico de custos nas estatísticas do Home Assistant.

## Problema

O motor de cálculo (`calculator.py`) está correcto. Validado contra a fatura
`042.DP.26080422002962699` (período 2026-07-11 ~ 2026-08-12, 20 m³, 33 dias):

```
limites prorrateados = [6, 11, 17, 28]
água 22,02 · saneamento 14,50 · resíduos 2,82 · taxas 3,94 · IVA 2,25
TOTAL 45,53 €   → bate ao cêntimo, linha por linha
```

O erro está em quem alimenta o motor. `cycle.cycle_length_days()` deriva o
comprimento do ciclo do `reset_day`, o que dá sempre o espaçamento
calendário-mês (28–31 dias). A AGERE fatura **entre datas de leitura**, que
derivam:

| Ciclo | Período facturado | Dias |
|---|---|---|
| jun→jul | 2026-06-13 ~ 2026-07-10 | 28 |
| jul→ago | 2026-07-11 ~ 2026-08-12 | 33 |
| ago→set (previsto) | 2026-08-13 ~ 2026-09-03 | ~22 |

Com `reset_day` a integração usaria 31 dias para o ciclo de julho→agosto:
total 46,99 € contra 45,53 € facturados (**+1,46 €**). Acresce que a janela de
consumo em HA ficaria desalinhada em 2 dias face à da fatura.

Sensibilidade a 20 m³ de consumo:

| Dias | Limites | Total | Δ vs fatura |
|---|---|---|---|
| 28 | 5, 9, 14, 23 | 49,34 € | +3,81 |
| 31 (modelo actual) | 5, 10, 16, 26 | 46,99 € | +1,46 |
| 33 (real) | 6, 11, 17, 28 | 45,53 € | 0,00 |

## Decisões

1. **O log de leituras é a única fonte de verdade.** Ciclos, sensores e
   estatísticas são todos derivados dele. Não se guardam ciclos calculados.
2. **O `reset_day` é removido.** Quebra de compatibilidade assumida: a
   integração está na v0.1.2 e acabou de entrar no HACS. A migração é sem
   perdas (ver *Migração*).
3. **O ciclo em curso usa uma data de leitura prevista**, copiada do campo
   *Período de Comunicação* da fatura. Sem ela, cai no comprimento do ciclo
   anterior e marca-se como estimado. Não se adivinha em silêncio.
4. **As leituras vivem em `entry.options`**, não num `Store`. O options flow
   escreve-as nativamente e o `add_update_listener` já existente recarrega a
   entrada e recalcula. O `Store` desaparece por completo.
5. **Duas fases.** Fase 1: log, serviços, opções, sensores. Fase 2:
   estatísticas históricas externas. O plano de implementação que sai deste
   spec cobre **apenas a fase 1**; a fase 2 recebe plano próprio depois de a
   fase 1 estar a funcionar e validada contra as faturas.

## Modelo de dados

Em `entry.options`, ao lado das opções tarifárias existentes:

```python
options = {
    "enable_water": True, "enable_sanitation": True,
    "enable_waste": True, "enable_taxes": True,
    "include_vat": True, "vat_rate": "0.06",
    "readings": [
        {"date": "2026-06-12", "m3": "2593", "source": "manual"},
        {"date": "2026-07-10", "m3": "2611", "source": "manual"},
        {"date": "2026-08-12", "m3": "2631", "source": "manual"},
    ],
    "next_reading_date": "2026-09-03",
}
```

- `m3` em string para preservar `Decimal`, como já se faz com `vat_rate`.
- `source` é `"manual"` (introduzida pelo utilizador) ou `"auto"` (criada pela
  integração no primeiro arranque ou pela migração). Serve para o menu
  distinguir as duas na lista.

Chaves novas em `const.py`: `CONF_READINGS`, `CONF_NEXT_READING_DATE`.
`CONF_RESET_DAY` e `DEFAULT_RESET_DAY` são removidos.

## Derivação de ciclos

`cycle.py` é substituído por `readings.py`, sem dependências do Home Assistant
(mesma regra do `calculator.py`).

```python
@dataclass
class Reading:
    date: date
    m3: Decimal
    source: str          # "manual" | "auto"

@dataclass
class Cycle:
    start: date
    end: date
    days: int
    consumption: Decimal
    estimated: bool

class ReadingLog:
    def add(...)  /  update(...)  /  remove(...)      # validados
    def closed_cycles(self) -> list[Cycle]
    def current_cycle(self, today, meter_total, next_reading_date) -> Cycle
```

Regras:

```
ciclo fechado i:   start = readings[i-1].date + 1 dia
                   end   = readings[i].date
                   days  = (readings[i].date - readings[i-1].date).days
                   m³    = readings[i].m3 - readings[i-1].m3
                   estimated = False

ciclo em curso:    start = última.date + 1 dia
                   end   = next_reading_date
                           ou start + (dias do ciclo anterior) - 1
                           ou start + 29                        (sem histórico)
                   days  = (end - start).days + 1
                   m³    = leitura_do_contador - última.m3
                   estimated = next_reading_date is None
```

Verificação contra as faturas: `07-10 → 08-12` dá 33 dias e 20 m³;
`06-12 → 07-10` dá 28 dias e 18 m³. Ambos coincidem com o facturado.

**Estado inicial sem leituras:** no primeiro `recompute()` com valor de contador
válido, a integração escreve uma leitura `(hoje, valor, source="auto")`. O
comportamento fica igual ao actual — primeiro ciclo parcial, 30 dias — sem
introduzir um conceito de *baseline* separado. A escrita é guardada por uma
verificação de existência para não entrar em ciclo de reload.

**Validação (`ReadingLog`):**

- datas estritamente crescentes, sem duplicados;
- `m3` não decrescente ao longo do tempo;
- `next_reading_date` estritamente posterior à última leitura;
- valores não negativos.

Violação levanta `ValueError` com mensagem específica. Os serviços devolvem-na
ao utilizador; o options flow mostra-a no formulário sem perder o input.

Apagar a última leitura existente é permitido: o log fica vazio e o próximo
`recompute()` volta a escrever uma leitura `source="auto"` com o valor corrente
do contador, como no estado inicial.

## Serviços

Registados em `__init__.py`, lógica em `services.py` novo, seletores em
`services.yaml`.

**`agere_water.set_reading`** — *upsert* pela data.

| Campo | Obrigatório | Notas |
|---|---|---|
| `date` | sim | `DateSelector`; é a **data da leitura / fim do período**, não a data de emissão da fatura |
| `m3` | não | omitido → lê das *long-term statistics* do sensor de origem (última hora do dia) |
| `config_entry` | não | `ConfigEntrySelector`; dispensável com uma única entrada |

Se a data já existir, substitui; senão insere ordenado. Com `m3` omitido e sem
dados nas LTS para essa data, erro explícito a pedir o valor — nunca se estima.

**`agere_water.remove_reading`** — `date`, `config_entry`.

**`agere_water.set_next_reading_date`** — `date` (omitido = limpar e voltar a
estimar), `config_entry`.

Três serviços em vez de quatro: mudar a **data** de uma leitura via serviço é
`remove_reading` + `set_reading`. Mudar datas é operação de UI e o menu fá-lo
num passo.

**Resposta (`SupportsResponse.OPTIONAL`)** — devolve o ciclo recalculado, para
comparar com a fatura na própria janela de *Ferramentas de desenvolvimento →
Ações*:

```yaml
cycle:
  start: 2026-07-11
  end: 2026-08-12
  days: 33
  consumption_m3: 20
  total: 45.53
  water: 22.02
  sanitation: 14.50
  waste: 2.82
  taxes: 3.94
  vat: 2.25
```

Mutação → `hass.config_entries.async_update_entry(entry, options=...)` → o
listener recarrega a entrada e os sensores actualizam.

## Options flow

```
init  (async_show_menu)
 ├─ "Leituras"                → readings
 ├─ "Próxima data de leitura" → next_reading
 └─ "Componentes e IVA"       → components
```

- **`components`** — o formulário actual de `config_flow.py`, sem o `reset_day`.
- **`next_reading`** — `DateSelector` + toggle "limpar e estimar".
- **`readings`** — `SelectSelector` com as leituras guardadas, rotuladas com o
  ciclo derivado, mais uma opção "nova leitura":

  ```
  2026-08-12 · 2631 m³ · 33 dias · 20 m³ · 45,53 €
  2026-07-10 · 2611 m³ · 28 dias · 18 m³ · 44,21 €
  ➕ Nova leitura
  ```

  Selecionar abre **`reading_edit`**: `date`, `m3` e um toggle "apagar esta
  leitura". Submeter valida, grava e volta ao `init`.

O HA não tem widget de lista editável; `SelectSelector` + passo de edição é o
padrão usado por integrações do core para o mesmo problema.

Custo assumido: cada alteração recarrega a entrada e recria as entidades, com um
instante de `unavailable`. Ocorre 2-3 vezes por mês.

## Sensores

`_AgereData` (em `sensor.py`) perde `Store` e `CycleManager` e passa a construir
o `ReadingLog` a partir das opções. Os três *listeners* (mudança de estado do
contador, meia-noite, reload) mantêm-se.

Atributos novos em `sensor.agere_total_cost`, somados aos actuais:

```
cycle_start: 2026-08-13
cycle_end: 2026-09-03
billing_days: 22
billing_days_estimated: false
next_reading_date: 2026-09-03
```

Sensor novo **`sensor.agere_last_invoice`** (`entity_category: diagnostic`):
estado = total do último ciclo **fechado**; atributos = ciclos derivados e log
de leituras.

```
state: 45.53
attributes:
  cycles:
    - {start: 2026-07-11, end: 2026-08-12, days: 33, m3: 20, total: 45.53}
    - {start: 2026-06-13, end: 2026-07-10, days: 28, m3: 18, total: 44.21}
  readings: [...]
```

É este sensor que torna a fase 1 útil por si só: introduzidas as faturas, o
histórico reconstruído fica visível sem depender da fase 2.

`sensor.agere_marginal_price` mantém a lógica; só recebe os novos `days`.

## Migração

`ConfigFlow.VERSION` 1 → 2, com `async_migrate_entry`.

Remover o `reset_day` sem mais faria perder a fronteira do ciclo e a *baseline*,
colapsando o total para os encargos fixos. O `Store` v1 guarda exactamente a
informação necessária para o evitar:

```
Store v1:  {cycle_start: "2026-08-13", baseline: "2631"}
      ↓
readings:  [{date: "2026-08-12", m3: "2631", source: "auto"}]
```

`date = cycle_start - 1 dia` reproduz a fronteira, `m3 = baseline` reproduz o
ponto de partida. Depois apaga-se o `Store` e remove-se `reset_day` das opções.
Entradas sem `Store` (nunca arrancadas) ficam com `readings: []` e caem no
estado inicial descrito acima.

## Fase 2 — estatísticas históricas

O painel de Energia e os gráficos leem **estatísticas**, que não se reescrevem
retroactivamente a partir do histórico de estados. Usa-se uma série externa,
que é reescrevível:

```python
statistic_id = "agere_water:total_cost"    # ":" marca-a como externa
has_sum = True; unit_of_measurement = "EUR"; source = "agere_water"
```

**Granularidade.** De uma fatura só se sabe o total do período, mas o motor
existente dá a curva acumulada sem alterações:

```
sum(dia_n) = calcular(m³_acumulados_até_dia_n, dias_do_ciclo, config).total
```

Os deltas diários saem correctos por construção: os escalões sobem no dia certo
e os encargos fixos caem no dia 1, como são facturados. Os m³ acumulados por dia
vêm das LTS do sensor do contador; onde não existirem, distribuem-se
uniformemente pelos dias do ciclo. Um único caminho de código.

**Reescrita.** Qualquer alteração ao log faz `async_clear_statistics` na série e
reimportação completa. Sem lógica incremental: 2-3 ciclos são ~90 pontos.
Editar uma data passada fica trivialmente correcto.

**A confirmar na implementação** (documentação do HA, não assumido aqui): se uma
estatística externa é selecionável como custo de água no painel de Energia. Se
não for, a série serve os gráficos e o histórico, e o painel continua ligado a
`sensor.agere_total_cost`.

**Duplicação.** `sensor.agere_total_cost` mantém as estatísticas próprias do
recorder. As duas séries coexistem; somar ambas no painel de Energia duplicaria
o custo. Fica documentado no README: uma ou outra, nunca as duas.

## Testes

TDD, testes antes da implementação.

- **`tests/test_readings.py`** (substitui `test_cycle.py`) — derivação de ciclos
  fechados e em curso, estimativa, validação, leitura sintética inicial.
- **`tests/test_calculator.py`** — acrescenta a terceira regressão de fatura:
  `20 m³ / 33 dias → 45,53 €`, com verificação por componente
  (22,02 / 14,50 / 2,82 / 3,94 / 2,25) e limites `[6, 11, 17, 28]`.
- **`tests/test_services.py`** (novo) — *upsert*, remoção, `m3` omitido com e
  sem LTS, erros de validação, conteúdo da resposta.
- **`tests/test_config_flow.py`** — menu, `reading_edit`, apagar, erros no
  formulário, e a migração v1→v2 a partir de um `Store` v1.
- **`tests/test_sensor.py`** — atributos novos, `agere_last_invoice`,
  recomputação após alteração de opções.

## Documentação

- **README:** reescrever *Configuration* (o *reset day* sai; entra a
  introdução de leituras), *Known limitation* (o primeiro ciclo parcial deixa de
  ser inevitável — resolve-se metendo a leitura da última fatura) e *Accuracy*
  (acrescentar a terceira fatura validada). Documentar que a data a introduzir é
  o fim do período de faturação, não a data de emissão.
- **CHANGELOG:** entrada de quebra de compatibilidade pela remoção do
  `reset_day`, com nota de que a migração é automática.

## Fora de âmbito

- Tradução PT das strings (não existe `translations/pt.json`; fica como
  acrescento independente).
- Edição dos valores tarifários pela UI (continua a exigir alteração de código).
- Importação automática de faturas AGERE (PDF ou portal).
