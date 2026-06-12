# Climatologia diária de Z500 — ERA5 1991–2020

Base climatológica da altura geopotencial em 500 hPa para a análise de **bloqueio
atmosférico** do CartoMet BR:

```text
Anomalia(dia, hora) = Z500_IFS(rodada, step) − z500_clim(dia-do-ano, hora)
```

## Conteúdo

- `z500_clim_<MMDD>.nc` — 366 arquivos (um por dia do ano, 29/02 incluído), dims
  `(hour: [0, 12], latitude, longitude)`, variável `z500_clim` em **gpm** (float32 + zlib).
- `manifest.json` — sha256, tamanho e metadados de cada arquivo. Permite ao aplicativo
  baixar **apenas o arquivo do dia** necessário e verificar a integridade.

## Especificação

| Item | Valor |
|:--|:--|
| Fonte | ERA5 (`reanalysis-era5-pressure-levels`), geopotencial `z` em 500 hPa |
| Período-base | 1991–2020 (normal climatológica OMM), 30 anos |
| Horários | 00Z e 12Z |
| Domínio | 150°W–30°E, 75°S–15°N |
| Grade | 0.25° × 0.25° — **idêntica ao IFS Open Data** (anomalia por subtração direta, sem regrid) |
| Suavização | Média anual + 4 primeiros harmônicos (FFT em série estrita de 365 dias; 29/02 interpolado da curva suave) |
| Unidade | gpm (`Z = z / 9.80665`) — mesma grandeza do `gh` do IFS Open Data |

Validação do produto: sem NaN; faixa [4863, 5909] gpm; continuidade dia-a-dia ≤ 4,5 gpm;
circularidade 31/12→01/01 com salto de 1,95 gpm; caso de bloqueio ômega de 20/05/2017
reproduzido (anomalia "A" de +327 gpm em ~52°S entre dois "B" em ~30°S).

## Licença e atribuição dos dados

Contém informação modificada do **Copernicus Climate Change Service (C3S) — ERA5**:

> *Generated using Copernicus Climate Change Service information [2026]. Neither the
> European Commission nor ECMWF is responsible for any use that may be made of the
> Copernicus information or data it contains.*

Referência: Hersbach, H. et al. (2020). The ERA5 global reanalysis. *Quarterly Journal
of the Royal Meteorological Society*, 146(730), 1999–2049. doi:10.1002/qj.3803.

A atribuição completa também está gravada nos atributos de cada NetCDF.
