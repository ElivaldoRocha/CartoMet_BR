# Metodologia — Análise de Bloqueio Atmosférico (Anomalia de Z500)

> Documentação técnica do CartoMet BR.
> Previsão do **ECMWF IFS** (Open Data) sobre climatologia **ERA5 1991–2020**.

---

## 1. O campo de diagnóstico

A ferramenta calcula a **anomalia de altura geopotencial em 500 hPa**:

$$
A(\lambda, \varphi) = Z_{500}^{IFS}(\text{rodada}, \text{step}) \;-\; \bar{Z}_{500}^{ERA5}(\text{dia-do-ano}, \text{hora})
$$

A climatologia remove o ciclo sazonal: sem ela, o verão apareceria como uma anomalia
positiva gigante e o inverno como negativa, mascarando os sistemas sinóticos. O que
sobra no campo $A$ são os **desvios do estado médio** — exatamente onde vivem as
cristas e os cavados anômalos que caracterizam padrões de bloqueio.

## 2. A climatologia de referência

| Item | Valor |
|:--|:--|
| Fonte | ERA5 (`reanalysis-era5-pressure-levels`), geopotencial em 500 hPa |
| Período-base | **1991–2020** (normal climatológica OMM vigente), 30 anos |
| Horários | 00Z e 12Z |
| Domínio | 150°W–30°E, 75°S–15°N |
| Grade | 0,25° × 0,25° — **idêntica ao IFS Open Data** (subtração direta, sem regrid) |
| Unidade | gpm ($Z = z / 9{,}80665$), a mesma do `gh` do IFS |

**Suavização harmônica.** As médias brutas por (dia-do-ano, hora) — ~30 amostras por
dia — são suavizadas retendo a **média anual + os 4 primeiros harmônicos** da série de
365 dias via FFT (padrão NCEP/CPC). A curva resultante é **circular por construção**
(sem salto na virada 31/12 → 01/01); o **29/02** é preenchido interpolando a curva
suave entre 28/02 e 01/03 (injetá-lo na FFT distorceria a fase do ciclo anual).

**Distribuição e integridade.** A climatologia (366 NetCDFs diários + `manifest.json`
com sha256) é publicada no repositório do CartoMet BR no GitHub. O aplicativo baixa
**apenas o arquivo do dia** necessário (~800 KB), verifica o sha256 contra o manifest
e mantém cache local (`climatologia/` no diretório de dados) — cliques seguintes não
tocam a rede. Sem autenticação: o repositório é público.

## 3. Interpretação operacional

- **Anomalias positivas intensas** ($\gtrsim +100$ gpm) e **persistentes** em latitudes
  médias-altas indicam uma crista anômala candidata a **bloqueio**; a persistência
  (vários dias/steps consecutivos) é critério essencial — um pico isolado num único
  horário é apenas uma crista transiente amplificada.
- O **bloqueio ômega (invertido no HS)** aparece como um **dipolo/tripolo**: anomalia
  positiva ("A") em ~45–60°S ladeada por duas negativas ("B") equatorward (~25–35°S).
- O **contorno do zero** (linha cinza-escura) delimita as áreas anômalas positivas e
  negativas — a fronteira crista/cavado anômalos.
- Para a **América do Sul**, bloqueios no Pacífico Sudeste/Atlântico Sul deslocam o
  jato e a trilha de tempestades, modulando estiagens e ondas de frio/calor — daí o
  domínio amplo (150°W–30°E), que mostra o trem de ondas a montante.

A decisão final é do previsor (*human-in-the-loop*): o campo orienta, mas a
identificação do bloqueio considera persistência, escala e o escoamento total.

## 4. Aproximação dos horários 06Z/18Z

A climatologia existe em **00Z e 12Z** (horários sinóticos principais). Para
valid_times em outras horas (rodadas 06Z/18Z ou steps intermediários), usa-se o
**horário climatológico mais próximo** (06–17h → 12Z; demais → 00Z, sempre com o
dia-do-ano do próprio valid_time). O ciclo diurno de Z500 é pequeno em escala
sinótica (poucos gpm), então o erro da aproximação é desprezível frente às anomalias
de interesse (dezenas a centenas de gpm). A carta sinaliza a aproximação com "≈".

## 5. Limitações

- O campo é **instantâneo**: não substitui índices de bloqueio com critério temporal
  (e.g., Tibaldi–Molteni); a persistência deve ser avaliada pelo previsor variando o
  *step*.
- A anomalia herda os erros de previsão do IFS no *step* escolhido (crescem com o
  horizonte).
- Comparações quantitativas finas IFS × ERA5 carregam pequenas diferenças
  sistemáticas entre modelo operacional e reanálise; para o diagnóstico sinótico de
  bloqueio (sinal de ±100–300 gpm), são de segunda ordem.

## 6. Dados e licença

Contém informação modificada do **Copernicus Climate Change Service (C3S) — ERA5**:

> *Generated using Copernicus Climate Change Service information [2026]. Neither the
> European Commission nor ECMWF is responsible for any use that may be made of the
> Copernicus information or data it contains.*

Previsões: **ECMWF Open Data** (CC BY 4.0 — atribuir ECMWF).

## Referências

- HERSBACH, H. et al. The ERA5 global reanalysis. *Quarterly Journal of the Royal
  Meteorological Society*, v. 146, n. 730, p. 1999–2049, 2020. doi:10.1002/qj.3803.
- TIBALDI, S.; MOLTENI, F. On the operational predictability of blocking. *Tellus A*,
  v. 42, n. 3, p. 343–365, 1990.
- REX, D. F. Blocking action in the middle troposphere and its effect upon regional
  climate. *Tellus*, v. 2, n. 3, p. 196–211, 1950.
- ECMWF. *IFS — Open Data documentation*. Reading: ECMWF, 2026.

---

*Documento de referência da análise de Bloqueio Atmosférico do CartoMet BR. A
construção e validação da climatologia (scripts, FFT, caso de 20/05/2017) está
documentada junto ao produto publicado em `climatology/z500/`.*
