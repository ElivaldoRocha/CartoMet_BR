# Changelog

Todas as mudanças notáveis do **CartoMet BR** são documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [3.1.0] — não lançado

### Corrigido

- **🧹 "Mesa branca" definitivamente domada (motor determinístico de layout).**
  Três efeitos que irritavam o uso diário foram eliminados na raiz:
  - **H/L flutuando na mesa** ao acionar o LOCZCIT (que encolhe o extent): os
    textos dos centros agora têm `clip_on=True` — texto do matplotlib não é
    recortado por padrão, e os centros "fora do mapa" eram desenhados sobre a
    mesa branca. O mesmo recorte foi aplicado aos **textos desenhados pelo
    usuário** (símbolos A/B, anotações, emoji fallback, régua de medição), que
    flutuavam da mesma forma ao navegar para outro domínio — ficou evidente ao
    importar um boletim WPC e auto-enquadrar na América do Norte.
  - **Mesa embaralhada no reset** com ZCIT + TSM na tela (carta à esquerda,
    colorbar órfã à direita, vão no meio): o `tight_layout` foi **aposentado** —
    ele ignora eixos de colorbar (não são subplots) e movia só a carta. O novo
    motor (`_layout_mesa`) posiciona tudo por construção: margens fixas para o
    gridliner, topo medido pelo título real, colorbars **ancoradas à caixa real
    da carta** (pós-`apply_aspect`) e centralização do conjunto; rede de
    segurança (`_fit_layout_to_figure`) agora cobre X **e** Y.
  - **Colorbars rebeldes**: o eixo criado por `fig.colorbar(ax=...)` re-impõe o
    aspect/shrink de criação dentro do próprio `set_position` (via
    `_ColorbarAxesLocator`), desfazendo qualquer ancoragem. As colorbars da
    ZCIT, do bloqueio e da TSM agora usam **`cax` explícito** (Axes comum, que
    obedece ao motor). Remover camadas devolve o espaço à carta
    (`remove_loczcit/remove_blocking(reflow=True)`).
  - **Menos travadas (renders sob controle)**: o motor mede o layout **sem
    renderizar** — `apply_aspect()` materializa a caixa real da carta e as
    tightbboxes saem dos transforms vivos (o Gridliner do Cartopy regenera os
    rótulos dentro do próprio `get_tightbbox`). Cada operação de camada
    (trocar step, adicionar campo, reset de extent, TSM…) custa agora
    **exatamente 1 render completo** — eram 4–5 com os `draw()` internos do
    reflow (medido: reset com pilha cheia caiu de 7,5 s para 1,4 s; adicionar
    campo, de ~1,8 s para ~0,5 s). A restauração pós-animação roda em lote
    (`batch_layout()`: um único reflow+render no fim) e a animação
    **suspende** o motor durante os quadros (a geometria congelada do
    controller é quem manda) — importante com rasters pesados (MUR SST 1 km).
    Régua de regressão: `_rascunhos/qa_smokes/perf_mesa.py` (conta renders por
    ação) e `visual_mesa_check.py` (geometria das colorbars preservada).

- **📡 Sonda Vertical observada de volta (UWyo aposentou o servidor antigo).**
  Em meados de 2026 a Universidade de Wyoming desligou o CGI legado
  (`/cgi-bin/sounding` → HTTP 404 permanente) e o `siphon` (≤ 0.10.x) ainda
  aponta para ele — a fonte **Observada (Wyoming)** passou ~1 semana
  retornando "Server Error (404)". Novo cliente próprio (`data/wyoming.py`)
  consome a **interface WSGI atual** (`/wsgi/sounding`, CSV em unidades SI),
  tentando **FM35** (TEMP clássico — paridade com o produto legado) e caindo
  para **BUFR** (alta resolução; único em estações que abandonaram o TAC).
  "Sem dados" é reconhecido pelo **corpo** da resposta (o status varia por
  fonte: FM35 → 404, BUFR → 400) e vira mensagem amigável de "balão não
  lançado", distinta de erro de servidor; o `siphon` permanece apenas como
  fallback de rede. **Bônus**: o CGI antigo servia vento em **nós** e o worker
  assumia m/s — o endpoint novo serve **m/s nativo** (e o fallback converte),
  corrigindo a magnitude das barbelas do Skew-T e do hodógrafo. Cobertura:
  `tests/test_wyoming.py` (fixture com resposta real de Belém 82193).

### Adicionado

- **🗺 "Mapinha" regional de um clique (feedback de usuário operacional —
  fluxo do antigo editor de cartas do SIPAM).** Três peças novas que juntas
  reproduzem o mapa-base regional georreferenciado sobre o qual o previsor
  carimba a simbologia:
  - **Recorte por estado** — combo *"Estado:"* no painel Região com as 27 UFs
    (`EXTENT_UFS` em `core/config.py`, caixas IBGE arredondadas para fora).
    Diferente do combo Região (que troca o domínio e limpa os dados),
    selecionar a UF apenas **recorta a carta atual** via `apply_extent`
    (dados preservados, layout correto, spinboxes sincronizados); zoom/pan
    manual desmarca a seleção sozinho.
  - **Camada "Cidades"** — checkbox em *Camadas sinóticas* que plota sedes
    municipais com nome (asset `assets/cidades_br.csv`: 2 678 cidades IBGE
    com população e flag de capital, gerado por `tools/gera_cidades_ibge.py`;
    geobr/geopandas **não** entram como dependência). Seleção determinística
    por vista: capitais > população, separação mínima proporcional à largura
    do domínio (mesma ideia do thinning de estações), halo branco no texto
    (legível sobre satélite). Seletor **Densidade** (Baixa / **Média** / Alta /
    Máxima) sob o checkbox, espelhando o das observações: o fator multiplica o
    teto de rótulos (7/14/28/56) e aproxima os rótulos entre si — em Rondônia,
    a Máxima recoloca Cacoal, Rolim de Moura, Costa Marques e Cerejeiras do
    mapa de referência do SIPAM. Replota em recorte, zoom, scroll/pan (no
    repouso) e troca de tema. *Nota de release:* o CSV precisa
    entrar nos data files do spec do PyInstaller, junto dos logos.
  - **"Destacar contornos"** — checkbox que engrossa costa/fronteiras/estados
    na cor forte do tema com **halo de contraste por baixo** (desenho duplo —
    sem path effects em patches sobre GeoAxes, a proibição histórica). Sobre a
    imagem de satélite as linhas finas do mapa base (estados com 0.2 pt)
    desapareciam e o previsor não sabia "em que região estava"; o realce
    **liga sozinho ao ativar o satélite** (e nunca se desliga sozinho). Cores
    `emphasis_line`/`emphasis_halo` por tema em `MAP_THEMES`; exige
    **cartopy ≥ 0.24** (FeatureArtist virou Collection — restilização
    in-place com `set_linewidth`/`set_edgecolor`).

- **🧭 Rosa dos ventos (indicador de norte).** Checkbox em *Camadas
  sinóticas* que desenha o padrão cartográfico clássico — triângulo preto +
  "N" — no canto superior direito da carta, para os mapas exportados.
  Implementada como **marker de Line2D + texto com halo** (geometria em
  pontos: não estica com o aspecto do extent; nada de `annotate(arrowprops=…)`
  ou patches de seta, proibidos em GeoAxes pela doutrina dos códigos
  poligonais). Sobrevive a troca de tema/região e é legível sobre satélite
  (borda branca + halo).

- **🌹 Rosa dos ventos (distribuição direção×velocidade).** Botão
  *"Rosa dos Ventos"* na toolbar (modo de clique exclusivo, como Meteograma/
  Sonda/Corte): clicar num ponto abre um painel dockável com a **distribuição do
  vento previsto** (IFS 10 m) ao longo dos steps da rodada — de onde sopra, com
  que intensidade e quanto de calmaria. Reusa o mesmo download do meteograma
  (`load_point_timeseries`, cache-first/anti-429) num `WindRoseWorker` em thread;
  a binagem (`data/wind_rose.py`, pura e testada) é setorial com faixas de
  velocidade, fração de calmaria no centro e direção dominante. Render **próprio**
  em eixo polar (`charts/wind_rose_plot.py`) — **sem** a dependência `windrose`,
  com controle total de tema e sem risco de projeção em GeoAxes. Combo *Setores*
  (8/16/36) **re-bina a série já baixada sem tocar na rede**. Badge de honestidade
  explícito: é a distribuição da **previsão**, **não** uma climatologia (mistura
  padrão sinótico + ciclo diurno de horas locais distintas). *Não confundir* com
  o indicador de norte acima (triângulo+N): aquele é decoração cartográfica, esta
  é análise estatística.
  - **Fixar no mapa (inset georreferenciado).** O botão *"📌 Fixar no mapa"* do
    painel ancora a rosa como um **inset polar compacto** nas coordenadas do ponto
    (`ax.inset_axes(..., transform=transData, projection="polar")`) — escala com o
    zoom e acompanha o *pan*, translúcido e legível sobre satélite (título com
    halo). Sobrevive à troca de tema/região e ao recorte (recriado do dado puro,
    como as cidades). **Some sozinha quando a âncora sai da área visível** (zoom-área/
    scroll/mover) — nada de rosa flutuando na "mesa branca" — e é **recortada ao
    retângulo da carta**. *"🗑"* (painel) e *"🗑 Limpar mapa"* removem as fixadas;
    teto de 8 para não pesar o *render*.
  - **Persistência no projeto (`.cmbr`).** As rosas fixadas entram no arquivo de
    projeto como **dado já binado** — abrir **nunca dispara rede**. O esquema do
    `.cmbr` sobe para **v2** (aditivo: projetos v1 continuam abrindo). Entram
    também no export PNG/PDF por serem parte da figura.
  - Níveis de pressão e rosa climatológica ERA5 ficam para uma fase seguinte.

- **📤 Boletim de Análise Codificado (CODSAS) — o traçado humano vira arquivo
  compartilhável.** Nenhuma instituição brasileira publica as posições das
  feições sinóticas em arquivo codificado, como o WPC/NOAA faz há décadas
  (*coded surface bulletin*, CODSUS). Agora o CartoMet exporta a análise
  traçada à mão num boletim de texto aberto — menu *Arquivo → "Exportar/
  Importar Boletim Codificado"* — pronto para compartilhar, arquivar ou montar
  um banco de análises da América do Sul. O formato **CODSAS V1**
  (`gui/bulletin_io.py`, módulo puro) preserva a estrutura do boletim do WPC
  (cabeçalho + `VALID` + uma feição por linha com continuação), mas usa
  coordenadas decimais assinadas `lat,lon` — o encoding compactado do WPC
  assume longitude sempre a OESTE e não representa o domínio da ZCIT (que
  cruza Greenwich até 15°E). Entram no boletim as 13 linhas OMM (`COLD`,
  `WARM`, `STNRY`, `OCFNT`, `ZCAS`, `ZCIT INTn`, `TROF`, `RIDGE`, `SQUALL`,
  `DRYLINE`, `FGEN`, `FLYS`, `JET` — frentes com `FLIP`), os 5 símbolos
  pontuais (`HIGH`, `LOW`, `HURCN`, `TSTORM`, `VORTEX`) e as anotações
  (`TEXT`); caneta, formas e emojis não são feições — permanecem no projeto
  `.cmbr` (que já persiste todos os desenhos). A importação é **aditiva e sem
  rede** (as feições entram no histórico — Desfazer funciona) e aceita também
  **boletins WPC genuínos** (via `metpy.io.parse_wpc_surface_bulletin`;
  pressões centrais viram rótulos), com **auto-enquadre** quando o boletim cai
  fora do enquadramento atual. Cobertura: `tests/test_bulletin_io.py`
  (round-trip completo, longitude leste, continuação de linha, CODSUS real).

- **🌡 Preset "Diagnóstico Baroclínico" (apoio ao traçado MANUAL de frentes).**
  Novo botão nas *Análises Prontas* que empilha, no nível escolhido (850 hPa
  por padrão, via diálogo), campos diagnósticos objetivos para o previsor
  traçar a frente à mão (*human-in-the-loop*): **Gradiente de θe** (sombreado)
  e **Eixo da Frente — TFP** (linha neutra-guia) já ligados; **Advecção de θe**,
  **θe** e **Frontogênese de Petterssen** empilhados e disponíveis (desligados).
  O eixo TFP é a isolinha *Thermal Front Parameter* = 0 (Hewson 1998) mascarada
  por `|∇θe|` mínimo — **guia de posição, não frente classificada** (a
  classificação fria/quente e o traçado OMM continuam com o meteorologista). Os
  campos de θe mascaram o terreno elevado (Andes: onde a pressão de superfície é
  menor que o nível, o θe é subterrâneo/fictício) para não gerar eixos-fantasma
  sobre a cordilheira, e também ficam disponíveis avulsos em *Campos em
  Altitude*. Substitui a abordagem de detecção/traçado automático (abandonada
  por não convergir com a análise sinótica humana).
- **🔑 Chave ERA5 (CDS) gerenciada pela interface** (menu *Arquivo → "Chave
  ERA5 (CDS)..."*). O usuário cola a chave pessoal (gratuita) da API do
  Copernicus/CDS sem tocar em terminal ou `~/.cdsapirc` — a credencial é
  passada por parâmetro ao `cdsapi.Client` e persistida via `QSettings`,
  com botão **"Apagar chave"** (pensado para laboratórios de ensino com
  computadores compartilhados), exibição mascarada (só os 4 últimos
  caracteres) e teste de conexão em thread (nunca trava a GUI). Aviso no
  diálogo: o ERA5 é publicado com ~5 dias de atraso. Novo extra opcional
  `reanalysis` (`cdsapi`); cadeia de resolução da chave (argumento → env
  `CDSAPI_KEY` → QSettings) em `data/cds_credentials.py` (módulo puro,
  reutilizável fora da GUI). Cobertura: `tests/test_cds_key.py`. Abre
  caminho para produtos baseados em reanálise (validação científica de
  índices e detecções automáticas).

- **⛰ Centros H/L blindados contra os Andes + ranqueamento por proeminência.**
  Dois refinamentos na detecção automática de centros de pressão da carta de
  superfície, motivados por avaliação A/B com PNMM real:
  - **Máscara orográfica** (nova sub-opção "⛰ Filtrar terreno elevado" sob
    *Centros H/L*, ligada por padrão; desligar re-renderiza na hora, sem rede):
    onde a redução ao nível do mar excede ~155 hPa (≈ 1500 m — `msl − sp`, com a
    pressão de superfície `sp` gratuita da stream `oper`), a PNMM é extrapolação
    sob a montanha e cria máximos/mínimos artefactuais no Altiplano/Andes. O
    limiar é o parâmetro calibrável `PNMM_ARTIFACT_DELTA_HPA` (ecmwf.py). Caches
    antigos sem `sp` degradam graciosamente (detector segue sem filtro).
  - **Ranqueamento por persistência topológica** (`metpy.calc.peak_persistence`,
    requer MetPy ≥ 1.7, agora mínimo do projeto): quando há mais candidatos que o
    teto de símbolos, os centros **proeminentes** vencem oscilações rasas — antes
    o critério era só o desvio de 1013 hPa. Silencioso (sem UI); com MetPy antigo
    cai no critério legado. O ranking é **cacheado por rodada/step**
    (`compute_persistence_maps`): o campo não muda entre replots por zoom/reset,
    e recalculá-lo a cada redesenho custava ~1,2 s. Nota: substituir o detector
    inteiro pela persistência foi avaliado e rejeitado (artefatos dos Andes;
    baixas subpolares conectadas ao cavado circumpolar têm persistência baixa)
    — o filtro morfológico continua sendo o detector; a persistência só ordena.
  - Cobertura: `tests/test_centers_detection.py` (máscara veta região; proeminência
    vence intensidade com teto de pontos; fallback sem máscara).

- **🌡️ Temperatura Máxima e Mínima a 2 m (`tmax2m`/`tmin2m`).** Novos campos de
  superfície no painel *Campos Meteorológicos*, direto do ECMWF Open Data gratuito
  (stream `oper`): `mx2t3`/`mn2t3` — o extremo das **últimas 3 horas** que antecedem
  o step (de +150h em diante o Open Data publica a janela de 6h, `mx2t6`/`mn2t6`, e
  o loader troca o parâmetro automaticamente — `t2_extreme_param`). Valores em °C
  (paletas `YlOrRd`/`YlGnBu_r`); step ≥ +3h obrigatório (no step 0 a janela é
  degenerada), validado na GUI e no diálogo de animação. GRIB de ~620 KB por step,
  cache-first como os demais campos. Cobertura: `tests/test_ecmwf.py::TestT2Extremes`.

- **🎬 Animação de Steps (GIF/MP4).** Novo **Arquivo → "Exportar Animação (GIF/MP4)…"**
  (`Ctrl+Shift+A`) e botão **"🎬 Animar Steps…"** no painel *Previsão*: a **composição
  atual do mapa** (sinótica, campos em altitude/presets, **Bloqueio Z500** e **ZCIT
  LOCZCIT-PA**) é re-renderizada para cada step do intervalo escolhido e exportada como
  **GIF** (sempre disponível, via Pillow) ou **MP4 H.264** (extra opcional `animation`:
  `uv sync --extra animation` — ffmpeg fica fora do instalador Windows). Satélite, TSM,
  observações e desenhos permanecem estáticos durante a animação.
  - **Prevenção estrutural do erro de alcance:** o diálogo só oferta steps válidos para
    a rodada (06Z/18Z ≤ +144h; 00Z/12Z ≤ +240h) — o erro "rodada 06Z tem alcance máximo
    de +144h" torna-se inalcançável pela animação. Passo nativo (3h; 6h após +144h) ou
    6/12/24h, com nota sobre a "aceleração" aparente ao cruzar +144h.
  - **Escala congelada entre quadros:** os níveis de contorno/colorbar dos campos são
    fixados a partir das estatísticas agregadas do intervalo inteiro
    (`FrameScaleTracker`), eliminando o "respirar" da colorbar quadro a quadro
    (bloqueio e raster da ZCIT já usam escalas fixas por design).
  - **Pipeline em 3 fases** com progresso unificado e cancelamento cooperativo:
    (1) pré-download serializado cache-first (anti-429; reusa o tratamento de retry
    existente — steps já baixados não vão à rede); (2) render dos quadros com preview
    ao vivo no canvas (produtor-consumidor: decode GRIB em worker, plot na GUI thread,
    1 step em voo); (3) codificação em streaming (RAM constante, quadros em disco).
    Em **qualquer** desfecho — sucesso, erro ou cancelamento — a composição original
    do mapa é restaurada e os quadros temporários são apagados.
  - **LOCZCIT-PA na animação:** se o índice ativo usa o filtro **LISA**, o diálogo
    avisa o custo (Monte Carlo por quadro) e sugere IQR via checkbox pré-marcado;
    o LISA permanece disponível por opt-in (a semente fixa garante consistência
    entre quadros). Com LOCZCIT na composição, o alcance ofertado é limitado a
    **+228h** (a OLR madura da Técnica B precisa do step `+12h` na rodada-base,
    inexistente além de +240h — evita 404 no fim do alcance).
  - Quadros com dimensões constantes (`MapCanvas.render_frame_png`, sem bbox "tight");
    paleta GIF mestra amostrada do intervalo (cores estáveis, sem "piscar" da
    colorbar); MP4 com dimensões pares (yuv420p) e `+faststart`. Núcleo puro e
    testável em `services/animation_service.py`, coberto por
    `tests/test_animation_service.py`.

### Corrigido

- **🔤 Título cortado após zoom de scroll/pan ("é uma coisa ou outra").**
  O reflow do layout já rodava nos caminhos de zoom-área/resetar/anterior
  (`apply_extent`), mas **scroll da roda e pan por arraste** faziam
  `set_extent + draw()` diretos — mudando a proporção da carta sem re-reservar
  o topo, o título saía cortado e só "voltava" ao resetar (perdendo o zoom).
  Agora um timer single-shot (~180 ms) re-arma a cada tick e **assenta a vista
  no repouso** do gesto: reflow (título re-medido) + replot das cidades.
  Barato (nada roda durante o gesto), respeita a animação
  (`_layout_suspended`) e não emite `extent_changed` (scroll/pan seguem sendo
  só visualização).

- **💧 Marca d'água "CartoMet BR" invisível conforme o fundo.** O texto era
  cinza chapado `#999999` sem contorno — sobre satélite, TSM ou campos de tom
  parecido, sumia. Ganhou o mesmo halo branco (`withStroke`) já usado nos
  rótulos de contorno, legível sobre qualquer fundo.

- **📐 Colorbar cortada na borda da "mesa" em extents panorâmicos.** A recentragem
  do layout media apenas as caixas dos eixos e ignorava os RÓTULOS — em enquadramentos
  largos (ex.: LOCZCIT + vento em zoom personalizado), os textos da colorbar
  ("Forte/Moderada/…") vazavam da figura e saíam cortados na tela, na exportação e nos
  quadros da animação. Novo passe `MapCanvas._fit_layout_to_figure()`: mede a união das
  *tightbboxes* (caixas **+ rótulos**), desloca o conjunto para caber e, se a união for
  mais larga que a figura, encolhe-a em torno do centro — a carta fica centralizada com
  a colorbar integralmente visível, automaticamente. No-op quando tudo já cabe (layouts
  bons não mudam).

## [3.0.2] — 2026-06-29

### Adicionado

- **🔭 Densidade ajustável das observações de superfície (SYNOP/METAR).** Novo seletor
  **Densidade** no painel *Observações de superfície* (Baixa / Média / Alta / **Máxima**),
  permitindo plotar muito mais estações — densidade estilo GEMPAK. Atende ao pedido do
  meteorologista **Gustavo C. J. Escobar**. O controle vale para SYNOP **e** METAR e
  **re-renderiza na hora a partir do cache** (sem novo download). O padrão passa a ser
  **Alta** (~2× mais estações que antes); o afinamento continua escalando com o zoom
  (mais detalhe ao aproximar). Internamente, o raio do `reduce_point_density` agora é
  modulado por um fator de densidade (`cartomet_br.data.stations.thinning_radius`), com
  piso reduzido de 0,25° → 0,10° para liberar as densidades altas. Regressão guardada por
  `tests/test_stations.py`.

### Garantia de qualidade

- **🔒 Blindagem do fix de datas (v3.0.1) nas análises prontas.** Auditoria confirmou que a
  correção da rodada selecionada (`cycle_date` → `date` do pedido ECMWF) propaga para **todas**
  as features que consomem dados do IFS — incluindo o **Índice ZCIT (LOCZCIT-PA)** e a **Análise
  de Bloqueio (Z500)**, e o caminho de **OLR desacumulada** (a técnica *Estabilizada* ancora a
  rodada-base madura derivada da selecionada, não "hoje"). Novos testes de regressão fecham a
  lacuna em que só o caminho sinótico estava coberto: `tests/test_ecmwf.py::TestDateAnchoringFeatures`.

## [3.0.1] — 2026-06-21

### Corrigido

- **🗓️ Título e dados presos na data de hoje ao trocar de rodada.** Ao selecionar uma
  rodada de um dia anterior (ex.: 19/06), o mapa continuava mostrando a data atual no
  título *e* baixava os campos do dia errado. Causa: várias chamadas de `download_ecmwf()`
  não passavam o parâmetro `date`, então o cliente do ECMWF Open Data baixava a rodada
  **mais recente** daquela hora de ciclo e a gravava sob o nome do dia selecionado — o GRIB
  vinha com a data errada, e o título (que lê fielmente o `valid_time`/`base_time` do GRIB)
  refletia o dado errado. Agora **todas** as chamadas ancoram `date=cycle_date`, preservando
  o modo automático (última rodada) quando nenhuma data é escolhida. Cobre o caminho de
  dados sinóticos (PNMM/espessura), níveis de pressão, variáveis derivadas, `skt`/`lsm` e
  `tcwv`. Regressão guardada por `tests/test_ecmwf.py::TestDateAnchoring`.

## [3.0.0] — 2026-06-17

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

### Adicionado (motor ZCIT, desenho e observações)

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
