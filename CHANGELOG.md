# Changelog

Todas as mudanças notáveis do **CartoMet BR** são documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado

- **🗂️ Carta OMM (exportação institucional):** novo **Arquivo → "Exportar Carta
  (OMM)…"** compõe, **somente no arquivo** (PNG/PDF), um **cabeçalho institucional**
  (instituição, analista, tipo de carta, validade/rodada/step auto-preenchidos, hora
  de emissão, logo opcional — defaults lembrados via `QSettings`) e uma **legenda
  apenas dos símbolos OMM efetivamente desenhados**. A mobília fica fora do mapa (não
  altera a geometria do Cartopy) e é incluída no recorte do export; a edição ao vivo e
  o "Salvar Imagem" cru permanecem intactos.
- **📈 Meteograma (série temporal num ponto):** clique num ponto → evolução do IFS em
  **+0…+72h** (T em 1000 hPa, vento de 10 m, precipitação por intervalo, PNMM e água
  precipitável) num painel docado de 4 eixos. Download **serializado por step**
  (cache-first, anti-429) em thread — a GUI nunca trava. Rótulo de honestidade
  (previsão **pontual do modelo**, aproximada).
- **🔪 Corte Vertical (cross-section A→B):** dois cliques no mapa definem a reta; o
  painel mostra a **seção pressão × distância** de ω (ascendência), temperatura,
  umidade e vento, por interpolação ao longo do caminho (13 níveis). Re-dispara ao
  mudar step/rodada.
- **🌩️ Campos de instabilidade (CAPE/CIN/LI/K):** novas camadas derivadas do modelo.
  **K-index** na grade nativa (vetorizado); **Lifted Index, CAPE e CIN** em grade
  **engrossada** (stride) e interpolados de volta, em thread com progresso. Render
  **contínuo** (níveis por percentil), **sem classes/limiares inventados**; campo
  totalmente indefinido é omitido. Rótulo "IFS, 13 níveis — aproximado".
- **🎈 Pseudo-sondagem do modelo (Skew-T do IFS):** a Sonda Vertical ganha a fonte
  **Modelo (IFS)** — perfil dos 13 níveis em **qualquer ponto** (inclusive oceano e
  steps de previsão), onde não há radiossonda; Skew-T, hodógrafa e índices com badge de
  honestidade.
- **💾 Projeto de análise (`.cmbr`):** salvar/abrir o **traçado manual + estado do
  mapa** (JSON versionado). Reabrir restaura **offline** (nunca dispara rede sozinho) e
  memoriza as camadas calculadas para reativação manual (*human-in-the-loop*).
- Infra docada comum (`gui/analysis_panel.py` → `AnalysisDock`; `gui/analysis_engine.py`
  → workers `QThread`) reusando os padrões da Sonda Vertical, com **exclusividade
  mútua** entre todos os modos de clique. Camadas de dados **GUI-free e testáveis**
  (`load_point_timeseries`, `load_cross_section`, `compute_instability_fields`); todos
  os downloads em `config.grib_dir` (nada solto/duplicado). +21 testes puros/offscreen.
- **🌀 Bloqueio Atmosférico (Z500):** nova Análise Pronta que calcula a **anomalia de
  altura geopotencial em 500 hPa** — `gh` do IFS (rodada + step) menos a climatologia
  diária **ERA5 1991–2020** (00Z/12Z, média anual + 4 harmônicos via FFT, setor
  150°W–30°E / 75°S–15°N). Render divergente (RdBu_r, níveis fixos de 40 gpm) com
  **contorno do zero** destacado e auto-enquadramento no setor; camada togglável/
  removível no painel. Anomalias positivas persistentes (≳ +100 gpm) em latitudes
  médias-altas sinalizam bloqueio; o padrão ômega aparece como dipolo A–B.
- **Climatologia distribuída pelo GitHub:** o produto (366 NetCDFs + `manifest.json`
  com sha256, em `climatology/z500/` no repositório) é baixado **por dia** (~800 KB),
  com verificação de integridade, retentativas com backoff e **cache local**
  (`climatologia/` no diretório de dados — coberto pela limpeza de cache). Offline com
  cache continua funcionando; horários 06Z/18Z usam o slot climatológico mais próximo
  (aproximação sinalizada com "≈" na carta).
- Novo motor `data/blocking_engine.py` (testável sem GUI e sem rede —
  `tests/test_blocking.py`) e `BlockingThread` dedicada: download e cálculo fora da
  thread da UI, com cancelamento cooperativo. O GRIB de `gh` 500 hPa **compartilha o
  cache** com a camada normal de geopotencial.
- Menu **Ajuda → "Sobre a Análise de Bloqueio (Z500)"** com resumo operacional e
  metodologia completa (`docs/Metodologia_Bloqueio_Z500.md`) aberta no navegador.
  Atribuição Copernicus/C3S (ERA5, Hersbach et al. 2020) embutida no produto e na
  documentação.

### Infraestrutura

- **CI corrigido e endurecido:** o workflow instalava as ferramentas de dev pelo
  grupo errado (`uv sync --dev` em vez de `--extra dev`), então `ruff`/`pytest`
  nunca rodavam (`Failed to spawn`). Corrigido; ambiente headless do Qt provisionado
  no runner Linux (libs `libegl1`/`libgl1`/… + `QT_QPA_PLATFORM=offscreen`) e backend
  do Matplotlib que degrada para Agg sem display.
- **Dívida de lint zerada:** `ruff check`/`ruff format` limpos em todo o pacote (≈450
  achados pré-existentes resolvidos, sem mudança de comportamento) e **bloqueantes** no
  CI.
- **Tipos endurecidos:** `mypy` limpo e **bloqueante** na camada de dados/lógica
  (anotações de causa-raiz em `VARIABLE_REGISTRY`/`MODOS`/dicts de `.sel`, guardas de
  `None`, `np.asarray` em retornos). O *glue* de renderização Cartopy (`map_canvas`,
  `charts/synoptic`, `charts/interactive`, `main_window`) é **isento** via
  `[tool.mypy.overrides]`: ali os `attr-defined` de `GeoAxes` são atrito de stub do
  Cartopy, não bugs.
- **Empacotamento do executável blindado:** o `cartomet_br.spec` passa a coletar via
  `collect_all` as bibliotecas de import dinâmico/lazy que a varredura estática do
  PyInstaller perdia silenciosamente — `esda`/`libpysal` (Coerência Espacial LISA),
  `statsmodels`/`patsy` (LOWESS do eixo da ZCIT), `siphon` (Skew-T Wyoming),
  `pymetdecoder` (SYNOP) e `markdown` (metodologia), além de reforçar
  `metpy`/`cfgrib`/`eccodes`. Sem isso, o `.exe` final mostrava o aviso *"Coerência
  Espacial não disponível"* de forma **permanente** (o usuário final não tem terminal
  para `uv sync --extra spatial`) e degradava outras features sem erro. A coleta é
  tolerante a falha (extra ausente apenas emite `[spec] AVISO:` e gera um exe sem o
  recurso, caindo no IQR — padrão da metodologia). `BUILD_EXECUTABLE.md` documenta o
  `uv sync --extra spatial` obrigatório antes do build.
- **Autoteste de empacotamento (`--selftest`) + gaps de dados fechados:** novo
  `cartomet_br/_selftest.py` importa **e exercita**, dentro do `.exe` congelado, todos
  os módulos que as features usam (backend de PDF/SVG, `metpy`/`pint`, `cfgrib`,
  engines do `xarray`, `siphon`, `pymetdecoder`, etc.), grava relatório em `%TEMP%` e
  mostra um diálogo de veredito — pegando "módulo não embarcado" **antes** do usuário
  (a classe do antigo crash ao salvar PDF). Rodar com `CartoMet_BR.exe --selftest`. O
  `.spec` passou a coletar **`pint`** (o `default_en.txt` que o `metpy.units` lê — sua
  ausência derrubaria Skew-T/instabilidade/LOCZCIT), **`pooch`** e **`xarray`**, e a
  embarcar os **metadados de entry-point** (`copy_metadata`) de `xarray`/`cfgrib`/
  `netCDF4` para a descoberta dos backends de leitura de GRIB/NetCDF. Coberto por
  `tests/test_selftest.py` (+6 testes).
- **Leitura de GRIB em clones Linux/macOS:** o wheel do `eccodes` só embarca a
  biblioteca binária no **Windows**; em Linux/macOS o `gribapi` cai no `findlibs`,
  que procura o pacote **`eccodeslib`** (e **não** o `ecmwflibs`). Sem ele, um
  `uv sync` num clone Linux deixava a leitura de GRIB quebrada (`Cannot find the
  ecCodes library`) — e o CI Linux falhava ao exercitar o autoteste. Adicionado
  `eccodeslib ; sys_platform != 'win32'` às dependências (o Windows segue usando o
  DLL embarcado no próprio `eccodes`). Com isso o autoteste roda **estrito** também
  no CI: se ficar verde no Linux, está provado que GRIB funciona num clone limpo.

## [3.0.0] — 2026-06-09

### Adicionado (reformulação científica — linhagem *Projeto ZCIT_AXIS*)

- **Máscara ATIVA acoplada + categoria Cinemática:** o portão da banda passa a ser a
  **união física** `oceano ∧ (OLR<240 ∨ C>C_THR)` (`C_THR=3e-5 s⁻¹`), que **resgata o
  ramo sul** da ZCIT — nítido na convergência do vento, cego na OLR. O raster ganha uma
  **4ª classe, Cinemática (0, magenta)**: banda real sustentada só por convergência, sem
  assinatura radiativa profunda (antes descartada como "céu limpo").
- **Envelope sazonal de latitude:** trava climatológica `φ_c(doy)=5°N+4.5°·cos(2π(doy−245)/365.25)`,
  faixa ±7.5°, que rejeita transientes subtropicais (VCAN/DOL) **sem** clipar o ramo sul.
- **Overlay opcional do EIXO da ZCIT:** detecção de banda **simples e dupla** (centroide
  ponderado + IQR de Tukey + LOWESS + bimodalidade + nó de bifurcação), oferecida como
  **camada togglável desligada por padrão** — orienta sem substituir o traçado manual.
  Novos módulos `data/zcit_axis.py` e `data/zcit_dual.py`; dependência `statsmodels`.

### Adicionado

- **✏ Caneta (traço livre):** desenho à mão livre na carta sinótica — pressione e
  arraste com o mouse ou **mesa digitalizadora** (o tablet age como mouse de precisão;
  arquitetura preparada para sensibilidade à pressão futura). Cor, espessura e opacidade
  customizáveis; decimação de pontos e `draw_idle` mantêm o traço fluido. Integrada ao
  undo/redo ([Z]/[Y]/Ctrl+Z) e ao Limpar.
- **⬜ Formas customizáveis:** Retângulo, Elipse/Círculo, Seta, Linha reta (arrastar com
  preview *rubber-band* ao vivo) e **Polígono livre** (clique nos vértices + Enter ou
  duplo-clique). Customização completa: cor da borda (8 presets + cor personalizada),
  preenchimento opcional, espessura, estilo de linha (sólida/tracejada/pontilhada) e
  opacidade. Render seguro em GeoAxes via doutrina dos **códigos poligonais** (`PathPatch`
  MOVETO/LINETO/CLOSEPOLY — sem códigos curvos). Esc cancela o rascunho; novo módulo
  `gui/draw_tools.py` com comandos serializáveis (prontos p/ "salvar desenhos" futuro).
  Ícones das ferramentas **desenhados via QPainter** (nítidos em qualquer SO — os glifos
  Unicode variavam com a fonte do sistema e ficavam ambíguos no Windows).
- **Índice ZCIT (LOCZCIT-PA — Potencial Acoplado):** índice integrado que localiza a
  Zona de Convergência Intertropical acoplando **∇TSM** (skin temperature oceânica),
  **convergência** do vento de baixos níveis (10 m) e **OLR desacumulada** (Técnica B,
  mitigação de *spin-up*), entregando um **raster categórico** (4 classes:
  Forte/Moderada/Fraca/Cinemática) como guia para o traçado manual da simbologia OMM
  (*human-in-the-loop*).
- **Coerência Espacial (LISA / Moran Local):** método **opcional** de delimitação da
  banda da ZCIT, alternativo ao IQR, escolhido por **modal** ao acionar o índice. Isola
  o *envelope* contíguo da convecção por *hotspots* estatisticamente significativos;
  reprodutível (semente fixa). Dependências no extra `spatial` (`esda`, `libpysal`);
  recai no IQR automaticamente se ausentes.
- **Mitigação da Camada Quente Diurna (DWL):** suavização Gaussiana *ciente-de-NaN* da
  skin temperature sobre o oceano **antes** do ∇TSM (mascara o continente primeiro, sem
  contaminar a costa — preserva a máscara `lsm ≤ 0.2`). Constante calibrável
  `SKT_SMOOTH_SIGMA`.
- **Observações de superfície (SYNOP/METAR)**, **Sonda Vertical (Skew-T Log-P)** e
  **zoom/pan interativo** no mapa — ver `README.md` para detalhes.
- Módulos `data/olr_timing.py` (fonte única da triangulação temporal da OLR) e
  `data/spatial_coherence.py` (filtro LISA).
- Testes de **caracterização** da desacumulação (âncora anti-regressão) e do filtro de
  Coerência Espacial (banda vs. órfãos, *fallback* sem dependências).

### Corrigido

- **Correções da campanha de QA (11/06/2026):** popup de erro **duplicado** no
  fluxo ZCIT (uma falha de rede exibia duas caixas idênticas); **marca d'água**
  movida para dentro da carta (colidia com o rótulo de longitude em todo export);
  `apply_extent` agora **clampa extents degenerados** (lat_min=lat_max esmagava a
  carta em silêncio) e documenta a ordem `[lon_min, lat_min, lon_max, lat_max]`;
  eixo da ZCIT plotado em **segmentos finitos** (NaN no `ax.plot` disparava
  `RuntimeWarning` do shapely a cada redraw); **colorbar do LOCZCIT alinhada à
  altura da carta** em domínios panorâmicos; endurecimento da Blindagem #7 com
  `set_antialiased(False)` explícito (no mpl 3.10 o kwarg cobre só o caminho
  rápido do `QuadMesh.draw`). Relatório completo:
  `_rascunhos/Relatorio_QA_CartoMet_BR_v3.0.0.md`.
- **Estrela da radiossondagem persistente:** o marcador da estação RAOB ancorada agora é
  removido ao **Limpar Mapa** (antes permanecia na carta).
- **Grupo de emojis "preso" visualmente:** ao entrar em zoom/pan/Sonda, o painel agora
  desmarca de fato o grupo de emojis (o `reset_emoji_mode` chamado pelo MainWindow não
  existia — no-op silencioso atrás de `hasattr`).
- **Carta maximizada na "mesa branca":** o reflow de layout (`tight_layout` +
  recentragem) que já rodava ao mudar o extent agora roda também no **startup** e na
  **troca de tema** — antes a carta ficava no retângulo padrão do matplotlib (~62% da
  largura útil na AmSul, com faixas brancas largas dos dois lados); agora ocupa o
  máximo que o aspecto geográfico e os rótulos permitem.
- **Botões "↩ Desfazer traço" e "↩ Desfazer forma":** desfazer dedicado por ferramenta
  nos grupos ✏ Caneta e ⬜ Formas (padrão do "↩ Desfazer emoji") — remove o último item
  daquele tipo mesmo que outros desenhos tenham vindo depois, sem afetar o redo.
- **Retângulo de zoom-área escondido:** o traço de seleção (vermelho tracejado) recebe
  `zorder` alto e aparece **por cima** de qualquer camada (GOES, campos em altitude)
  durante o arraste.
- **Desfazer emoji:** novo botão "↩ Desfazer emoji" no painel de simbologias remove o
  último emoji colocado, independente do histórico de desenhos.
- **Consistência temporal da OLR (P0):** a desacumulação genérica (`load_olr` /
  `load_precip`) agora **ancora a data da rodada** na requisição de rede — antes baixava
  a rodada "mais recente" e podia rotular o cache com data incorreta.
- **Anti-404 pós-144 h (P1):** o *step*-alvo da Técnica B é arredondado à **grade
  publicada do IFS** (3 h até 144 h; 6 h além), evitando *steps* inexistentes
  (ex.: 147, 153) que retornavam HTTP 404.
- **Rodadas 06Z/18Z (IFS Cycle 50r1):** suporte ao stream `oper` (`scda`/`scwv`
  descontinuados); exige `ecmwf-opendata >= 0.3.29`.

### Modificado

- **Triangulação temporal unificada (DRY):** a regra de tempo da OLR madura tem agora
  uma **fonte única** (`olr_timing.resolve_olr_window`), compartilhada pelo motor e por
  `ecmwf._resolve_accum_window`, com *fallback* de data robusto.
  `plan_olr_deaccumulation` mantido como **alias** de compatibilidade.
- **Documentação científica** (`docs/Metodologia_LOCZCIT-PA.md`) harmonizada com o
  código: vocabulário de *spin-up* (desacumulação **intra-rodada**), justificativa
  honesta da `skt`/DWL, pesos iguais ancorados em **Dawes (1979)**, natureza
  **relativa por meridiano** da normalização, ressalva ao IQR (DOL/VCAN são *acoplados*
  à ZCIT, não *outliers*) e documentação do método LISA.

## [2.2.0] — 2026

- Emojis meteorológicos coloridos no mapa, botão "Aplicar Região" e novos contextos
  ambientais/climáticos. Ver `README.md` (Novidades da v2.2).

## [2.1.0] — 2026

- TSM MUR SST 1 km (NASA/NOAA via ERDDAP), Convergência de Umidade (MFC), importação
  offline de GRIB2, captura *pixel-perfect* e feedback de HTTP 429 na GUI.
  Ver `README.md` (Novidades da v2.1).

## [2.0.0] — 2026

- Satélite GOES-East, sistema de camadas independentes, 15 variáveis meteorológicas,
  presets de análise e ferramentas pedagógicas. Ver `README.md` (Funcionalidades v2.0).

[3.0.0]: https://github.com/ElivaldoRocha/CartoMet_BR/releases/tag/v3.0.0
[2.2.0]: https://github.com/ElivaldoRocha/CartoMet_BR/releases/tag/v2.2.0
[2.1.0]: https://github.com/ElivaldoRocha/CartoMet_BR/releases/tag/v2.1.0
[2.0.0]: https://github.com/ElivaldoRocha/CartoMet_BR/releases/tag/v2.0.0
