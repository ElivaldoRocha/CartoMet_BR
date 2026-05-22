<p align="center">
  <img src="cartomet_br/assets/CartoMet_BR_logo_400_400.png" alt="CartoMet BR Logo" width="200"/>
</p>

<h1 align="center">CartoMet BR</h1>

<p align="center">
  <strong>Cartografia Meteorológica para o Brasil</strong><br/>
  Análise sinótica interativa com dados ECMWF Open Data, imagens GOES-East e TSM MUR SST 1km
</p>

<p align="center">
  <a href="#-sobre">Sobre</a> •
  <a href="#-vídeo-demonstrativo">Vídeo</a> •
  <a href="#-novidades-da-v22">Novidades v2.2</a> •
  <a href="#-funcionalidades">Funcionalidades</a> •
  <a href="#-download">Download</a> •
  <a href="#-instalação">Instalação</a> •
  <a href="#-documentação">Documentação</a> •
  <a href="#-autores">Autores</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/versão-2.2.0-blue?style=for-the-badge" alt="Versão"/>
  <img src="https://img.shields.io/badge/plataforma-Windows%2010%2F11-0078D6?style=for-the-badge&logo=windows" alt="Windows"/>
  <img src="https://img.shields.io/badge/licença-MIT-green?style=for-the-badge" alt="Licença"/>
  <img src="https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
</p>

---

## Sobre

O **CartoMet BR** é uma ferramenta profissional de cartografia meteorológica desenvolvida especificamente para **análise sinótica** no Brasil e América do Sul. O software permite visualizar campos meteorológicos do modelo **ECMWF IFS** em qualquer nível de pressão, sobrepor **imagens de satélite GOES-East** (Banda 13 IR) e **Temperatura da Superfície do Mar** (MUR SST 1km — NASA/NOAA), desenhar simbologias padronizadas WMO e aplicar presets de diagnóstico operacional — incluindo o critério de detecção de ZCAS do CPTEC/INPE (Escobar, 2019).

### Motivação

Este software foi idealizado e desenvolvido como um **presente e expressão de gratidão** às instituições de origem e formação dos autores:

- **UFPA** — Universidade Federal do Pará
- **IG** — Instituto de Geociências
- **FAMET** — Faculdade de Meteorologia
- **PPGGRD** — Programa de Pós-Graduação em Gestão de Riscos e Desastres na Amazônia

O objetivo é oferecer uma ferramenta gratuita que possa ser utilizada em **sala de aula**, auxiliando no ensino de análise sinótica e meteorologia, e também por **profissionais** em suas atividades operacionais de previsão do tempo.

---

## Vídeo Demonstrativo

<p align="center">
  <a href="https://www.youtube.com/watch?v=DNMAWe7L1wA">
    <img src="https://img.youtube.com/vi/DNMAWe7L1wA/maxresdefault.jpg" alt="CartoMet BR - Vídeo Demonstrativo" width="600"/>
  </a>
</p>

<p align="center">
  <a href="https://www.youtube.com/watch?v=DNMAWe7L1wA">
    <img src="https://img.shields.io/badge/▶_Assistir_no_YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="Assistir no YouTube"/>
  </a>
</p>

---

## Novidades da v2.2

### Emojis Meteorológicos Coloridos no Mapa

- **36 emojis** organizados em 6 categorias: Céu/nuvens, Precipitação, Fenômenos extremos, Frio/inverno, Calor/seca, Estações do ano e Outros
- Renderização full-color via pipeline nativo do Qt (Segoe UI Emoji / Apple Color Emoji / Noto Color Emoji) — sem preto/monocromático
- Painel com ícones coloridos e identificáveis (tooltip com nome ao passar o mouse)
- Tamanhos P / M / G configuráveis
- Emojis ancorados às coordenadas geográficas corretas no mapa
- Desfazer / Limpar funcionam inclusive quando há campos sinóticos carregados

### Botão "Aplicar Região"

- Novo botão no painel de configurações: após editar manualmente os campos Lon Min/Max e Lat Min/Max, clique **↻ Aplicar Região** para atualizar o mapa imediatamente sem precisar alterar o combo de regiões predefinidas

### Novos contextos ambientais e climáticos

- Adicionados emojis de **queimada** (🔥), **seca** (🌵 🏜), **ciclone tropical** (🌀), **vulcão/cinzas** (🌋), **calor extremo** (🥵), **frio extremo** (🥶), **neve em altitude** (🏔), além das quatro estações (🌸 🌻 🍂 ⛄)

---

## Novidades da v2.1

### Temperatura da Superfície do Mar (TSM) — MUR SST 1km

- **Nova fonte de dados**: NASA/NOAA MUR SST via ERDDAP server-side subsetting
- Resolução nativa 0.01° (~1 km) — a mais alta resolução SST operacional disponível
- Download otimizado: recorte feito no servidor (ERDDAP), não via OPeNDAP lento
- Progresso real em bytes durante o download (~16 MB para América do Sul)
- Paleta `RdYlBu_r` com colorbar horizontal dedicada
- Painel próprio no dock esquerdo (abaixo do Satélite GOES) com seletor de data e toggle
- Cache local automático por data — reutiliza sem re-download
- Integração com título dinâmico do mapa (sozinha ou combinada com outras camadas)

### Convergência de Umidade (MFC) — Variável derivada
- Calculada a partir de q, u e v: `MFC = -(adv_q + q * div_v)`
- Disponível em qualquer nível de pressão no painel "Campos em Altitude"
- Paleta BrBG simétrica (convergência positiva = convecção favorecida)

### Painel de Camadas Reorganizado
- **Campos em Altitude**: 13 variáveis (t, r, q, gh, wind, isotacas, w, d, vo, adv. T, grad. T, frontogênese, MFC)
- **Campos de Superfície/Integrados**: OLR e Água Precipitável — botão dedicado, sem necessidade de selecionar nível

### Importação Offline de Arquivos GRIB2
- Menu **Dados → Importar Arquivo Local** (Ctrl+I)
- Reconhece o padrão de nome ECMWF do CartoMet e restaura o contexto completo:
  - Arquivo MSL → reconstrói carta sinótica com PNMM + Espessura automaticamente
  - Arquivo PL/Superfície → injeta camada via pipeline oficial (conversões, suavização, colorbar)

### Captura Pixel-Perfect do Mapa
- **Ctrl+P** — Captura o mapa exatamente como exibido na tela (via `QWidget.grab`)
- **Ctrl+S** — Exportação tradicional via `savefig` (200 DPI, layout recalculado)

### Título Dinâmico do Mapa
- Atualiza automaticamente ao adicionar, ocultar ou remover camadas
- Linha 1: campo ativo + base sinótica (ex: "Temperatura 850 hPa (°C) + PNMM (hPa)")
- Linha 2: `Rodada: 12Z 24/03/2026 | Step: +3h | Válido: 2026-03-24T15:00 UTC`

### Feedback de HTTP 429 na GUI
- Intercepta retries automáticos do ECMWF e exibe na barra de progresso
- Mensagens claras: "Servidor ocupado (HTTP 429) — tentativa N, aguardando 2min..."
- Label de status em vermelho durante retries, laranja durante progresso normal

### Seletor de Minutos no Satélite
- Campo de minutos (00, 10, 20, 30, 40, 50) para seleção precisa de imagens GOES
- Validação com aviso se horário futuro selecionado

---

## Funcionalidades da v2.0

### Imagem de Satélite GOES-East
- Download direto do AWS S3 (NOAA) — sem autenticação
- Banda 13 (IR 10.3μm) com paleta clássica IR AVHRR
- Seletor de data e hora (00Z a 23Z) com validação
- Suporte a GOES-19 (operacional) com fallback para GOES-16
- Cache automático de arquivos já baixados

### Sistema de Camadas Independentes
- Empilhamento livre de camadas com toggle instantâneo (liga/desliga sem re-download)
- Colorbar inset que não rouba espaço do mapa
- Zorder crescente com simbologias sempre no topo

### 15 Variáveis Meteorológicas

| Variável | Descrição | Unidade |
|----------|-----------|---------|
| `t` | Temperatura | °C |
| `r` | Umidade Relativa | % |
| `q` | Umidade Específica | g/kg |
| `gh` | Geopotencial | mgp |
| `wind` | Vento (barbelas, vetores ou correntes) | kt |
| `wind_speed` | Isotacas (velocidade do vento) | km/h |
| `w` | Velocidade Vertical (ω) | hPa/h |
| `d` | Divergência | ×10⁻⁵ s⁻¹ |
| `vo` | Vorticidade Relativa | ×10⁻⁵ s⁻¹ |
| `olr` | Radiação de Onda Longa (OLR) | W/m² |
| `tcwv` | Água Precipitável | mm |
| `temp_adv` | Advecção de Temperatura | °C/h |
| `temp_grad` | Gradiente de Temperatura | °C/100km |
| `frontogenesis` | Frontogênese de Petterssen | °C/100km/3h |
| `mfc` | Convergência de Umidade (MFC) | ×10⁻⁵ g/kg/s |

### Presets de Análise para Sala de Aula

| Preset | Camadas |
|--------|---------|
| **Sinótica clássica** | Vento 850 vetores + Advecção de T 850 |
| **Jato e divergência** | Vento 250 correntes + Isotacas 250 + Divergência 200 |
| **Baixos níveis** | Vento 925 vetores + Advecção de T 925 + UR 850 |
| **Convecção profunda** | Divergência 200 + Omega 500 + Vorticidade 850 |
| **ZCAS (Escobar)** | Água Precipitável + Vento 850 vetores + Omega 500 |

### Ferramentas Pedagógicas
- **Anotações no mapa** — Clique e digite "ZCIT", "JBN", "ASAS", "Zona Baroclínica", etc.
- **Régua de distância** — Meça distâncias em km entre pontos (fórmula de Haversine)
- **Modos exclusivos** — Desenho, Anotação e Régua não se misturam

### Interface
- Janela de boas-vindas com créditos e logos institucionais
- 6 temas de mapa: Clássico, Branco, Pastel, Tons de cinza, Terra, Escuro
- Seletor de rodadas ECMWF com estimativa de disponibilidade
- Diálogo de progresso com barra de download e botão cancelar
- Limpeza automática de arquivos parciais ao cancelar download

---

## Resumo de Recursos

| Recurso | Descrição |
|---------|-----------|
| **Dados ECMWF** | Download automático de dados gratuitos do modelo IFS (resolução 0.25°) |
| **Satélite GOES-East** | Imagem IR Banda 13 com paleta clássica, seleção por data/hora/minuto |
| **TSM — MUR SST 1km** | Temperatura da Superfície do Mar operacional (NASA/NOAA via ERDDAP) |
| **Carta de Superfície** | PNMM, Espessura 1000-500 hPa, Centros H/L automáticos |
| **Campos em Altitude** | 15 variáveis em qualquer nível de pressão (925, 850, 700, 500, 300, 250, 200 hPa) |
| **Variáveis Derivadas** | Advecção de T, Gradiente de T, Frontogênese, MFC |
| **Variáveis de Superfície** | OLR (paleta clássica), Água Precipitável |
| **Camadas Independentes** | Empilhe campos livremente com toggle liga/desliga |
| **Presets de Análise** | 5 combinações prontas para sala de aula, incluindo ZCAS (Escobar/CPTEC) |
| **10 Simbologias WMO** | Frentes, ZCAS, ZCIT, Cavado, Crista, LI, Dryline |
| **Importação Local** | Restauração offline de arquivos GRIB2 baixados previamente |
| **Anotações** | Texto livre sobre o mapa para identificar sistemas |
| **Régua** | Medição de distância em km |
| **6 Temas** | Clássico, Branco, Pastel, Cinza, Terra, Escuro |
| **Regiões** | América do Sul, Brasil, Nordeste, Sudeste, Sul |
| **Exportação** | PNG, JPEG ou PDF (Ctrl+S) + Captura pixel-perfect (Ctrl+P) + seletor de DPI |
| **Emojis Meteorológicos** | 36 emojis coloridos (clima, estações, fenômenos extremos) ancorados no mapa |

---

## Download

### Versão Atual: 2.2.0

| Arquivo | Descrição | Download |
| --- | --- | --- |
| **Instalador_CartoMet_BR_v2.2.0.exe** | Instalador para Windows | [Download](https://github.com/ElivaldoRocha/CartoMet_BR/releases/latest) |
| **CartoMet_BR_Manual_Usuario.pdf** | Manual do usuário ilustrado | [Download](https://github.com/ElivaldoRocha/CartoMet_BR/releases/latest/download/CartoMet_BR_Manual_Usuario.pdf) |

> **Dica:** Baixe também o manual para aprender todas as funcionalidades do programa.

---

## Instalação

### Método 1: Instalador Windows (Recomendado para Usuários Windows)

1. Baixe `Instalador_CartoMet_BR_v2.2.0.exe` na seção [Releases](https://github.com/ElivaldoRocha/CartoMet_BR/releases/latest)
2. Execute o instalador e siga as instruções
3. Abra o CartoMet BR pelo atalho no Menu Iniciar ou Desktop

### Método 2: Código-Fonte — Windows, Linux e macOS

#### Pré-requisitos

| Dependência | Por quê |
|-------------|---------|
| **Python 3.12+** | Linguagem do projeto |
| **Git** | Para clonar o repositório |
| **GEOS / PROJ / eccodes** | Bibliotecas C exigidas pelo Cartopy e cfgrib (veja abaixo) |

#### Windows

```bash
# Clone o repositório
git clone https://github.com/ElivaldoRocha/CartoMet_BR.git
cd CartoMet_BR

# Instale com UV (recomendado)
uv sync

# Execute
uv run python -m cartomet_br gui
```

```bash
# Alternativa com pip
pip install -e .
python -m cartomet_br gui
```

> **Nota:** No Windows, o `pip install` geralmente resolve GEOS/PROJ/eccodes automaticamente via wheels pré-compilados.

#### Linux (Ubuntu/Debian)

```bash
# 1. Instale as dependências de sistema
sudo apt update
sudo apt install -y libgeos-dev libproj-dev libgl1-mesa-glx libegl1
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone e execute
git clone https://github.com/ElivaldoRocha/CartoMet_BR.git
cd CartoMet_BR
uv sync
uv run python -m cartomet_br gui
```

> **Nota:** O `uv` detecta o arquivo `.python-version` e baixa o Python 3.12.11 automaticamente — não é necessário instalar Python manualmente.

> **Nota:** O PyQt6 requer `libEGL`. Se a GUI não abrir, instale `libegl1` (Debian/Ubuntu) ou `mesa-libEGL` (Fedora).

<details>
<summary><strong>Fedora / RHEL / CentOS</strong></summary>

```bash
sudo dnf install -y geos-devel proj-devel mesa-libGL mesa-libEGL
curl -LsSf https://astral.sh/uv/install.sh | sh
```

</details>

<details>
<summary><strong>Arch Linux</strong></summary>

```bash
sudo pacman -S geos proj mesa
curl -LsSf https://astral.sh/uv/install.sh | sh
```

</details>

#### macOS

```bash
# 1. Instale as dependências com Homebrew
brew install geos proj uv

# 2. Clone e execute
git clone https://github.com/ElivaldoRocha/CartoMet_BR.git
cd CartoMet_BR
uv sync
uv run python -m cartomet_br gui
```

> **Nota:** O `uv` detecta o `.python-version` e baixa o Python 3.12.11 automaticamente — não é necessário `brew install python@3.12`. Funciona nativamente em Apple Silicon (M1/M2/M3/M4).

> **Atenção:** Não use `pip install -e .` diretamente — o pip ignora o `uv.lock` e pode baixar versões de `eccodes`/`cartopy` com ABI incompatível. Sempre use `uv sync`.

#### Com UV (qualquer plataforma)

```bash
# UV detecta a plataforma e resolve dependências automaticamente
git clone https://github.com/ElivaldoRocha/CartoMet_BR.git
cd CartoMet_BR
uv sync
uv run python -m cartomet_br gui
```

### Primeira Execução

Na primeira execução, o programa exibirá uma **janela de boas-vindas** e solicitará que você escolha um **diretório de dados** para armazenar os arquivos meteorológicos e de satélite baixados.

> **Recomendação:** Escolha uma pasta dentro de "Documentos", como `Documentos/CartoMet_BR_Data`. Evite pastas do sistema.

---

## Requisitos

| Requisito | Mínimo | Recomendado |
|-----------|--------|-------------|
| **Sistema Operacional** | Windows 10, Ubuntu 22.04, macOS 13+ (64-bit) | Windows 11, Ubuntu 24.04, macOS 14+ |
| **Python** | 3.12 | 3.12+ |
| **Memória RAM** | 4 GB | 8 GB |
| **Espaço em Disco** | 500 MB | 2 GB+ (para dados e satélite) |
| **Conexão** | Internet para download ECMWF, GOES e MUR SST | Banda larga |

---

## Atalhos de Teclado

| Tecla | Ação |
|-------|------|
| `Ctrl+S` | Salvar imagem (savefig — layout recalculado) |
| `Ctrl+P` | Capturar tela do mapa (pixel-perfect) |
| `Ctrl+I` | Importar arquivo GRIB2 local |
| `Ctrl+N` | Novo projeto (reinicia) |
| `Ctrl+Z` | Desfazer desenho |
| `Ctrl+Y` | Refazer desenho |
| `1`–`0` | Simbologias (Frentes, ZCAS, ZCIT, Cavado, etc.) |
| `F` | Inverter símbolos |
| `Enter` | Finalizar linha |
| `Z` | Desfazer ponto |
| `C` | Limpar tudo |

---

## Tecnologias

- **Python 3.12** — Linguagem de programação
- **PyQt6** — Interface gráfica
- **Matplotlib** — Visualização de dados
- **Cartopy** — Projeções cartográficas
- **xarray + cfgrib** — Leitura e manipulação de dados GRIB2
- **SciPy** — Suavização gaussiana e processamento numérico
- **MetPy** — Processamento meteorológico
- **ECMWF Open Data** — Fonte de dados meteorológicos
- **NOAA GOES-East (AWS S3)** — Imagens de satélite
- **NASA/NOAA MUR SST (ERDDAP)** — Temperatura da Superfície do Mar (1 km)

---

## Fontes de Dados

### ECMWF Open Data
- **Modelo**: IFS (Integrated Forecasting System)
- **Resolução**: ~0.25° (HRES)
- **Atualizações**: 4x ao dia (00, 06, 12, 18 UTC)
- **Alcance**: até 10 dias (144h em steps de 3h, depois 6h)
- **Licença**: [CC BY 4.0](https://www.ecmwf.int/en/forecasts/datasets/open-data)

### NOAA GOES-East
- **Satélite**: GOES-19 (operacional) / GOES-16 (legado)
- **Instrumento**: ABI (Advanced Baseline Imager)
- **Banda**: 13 — IR 10.3μm (temperatura de brilho)
- **Resolução temporal**: Full Disk a cada 10 minutos
- **Fonte**: [AWS S3 noaa-goes19](https://registry.opendata.aws/noaa-goes/)
- **Licença**: Domínio Público

### NASA/NOAA MUR SST

- **Dataset**: Multi-scale Ultra-high Resolution SST (MUR) v4.1
- **Resolução espacial**: 0.01° (~1 km) — a mais alta resolução SST operacional disponível
- **Cobertura**: Global, quasi-diária (latência ~2 dias)
- **Variável**: `analysed_sst` — temperatura em °C (CF conventions)
- **Acesso**: ERDDAP server-side subsetting (download direto do recorte como NetCDF)
- **Fonte**: [CoastWatch ERDDAP — jplMURSST41](https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.html)
- **Licença**: Domínio Público (NASA/NOAA)

---

## Estrutura do Projeto

```
CartoMet_BR/
├── cartomet_br/
│   ├── __init__.py              # Versão e exports
│   ├── __main__.py              # Entry point CLI
│   ├── core/
│   │   └── config.py            # Configurações e validação
│   ├── data/
│   │   ├── ecmwf.py             # Download ECMWF, GOES, VARIABLE_REGISTRY
│   │   └── sst.py               # Download MUR SST 1km via ERDDAP
│   ├── symbols/
│   │   ├── base.py              # Classe base e helpers
│   │   ├── fronts.py            # Frentes (fria, quente, etc.)
│   │   ├── effects.py           # ZCAS, ZCIT, cavado, crista, LI, etc.
│   │   └── point_symbols.py     # Ciclone, tempestade tropical, vórtice
│   ├── charts/
│   │   ├── synoptic.py          # Geração de carta sinótica
│   │   └── interactive.py       # Ferramenta interativa matplotlib
│   ├── services/
│   │   └── data_service.py      # Camada de serviço (validação, logging)
│   ├── gui/
│   │   ├── main_window.py       # Janela principal (orquestrador)
│   │   ├── map_canvas.py        # Canvas Matplotlib/Cartopy
│   │   ├── drawing_panel.py     # Painel de simbologias
│   │   ├── layer_panel.py       # Painéis de camadas e configurações
│   │   ├── download_dialog.py   # Threads de download e diálogo de progresso
│   │   ├── dialogs.py           # Welcome, FirstRun
│   │   ├── themes.py            # Temas visuais e estilos
│   │   └── _constants.py        # Metadados e caminhos de assets
│   └── assets/
│       ├── CartoMet_BR_logo_*   # Logos e ícones
│       └── Logos_UFPA_IG_FAMET_PPGGRD.png
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_data_service.py
│   ├── test_drawing_history.py
│   ├── test_ecmwf.py
│   ├── test_interactive.py
│   └── test_symbols.py
├── output/
│   └── instalador_script.iss    # Script Inno Setup para gerar instalador
├── .github/
│   └── workflows/
│       └── ci.yml               # CI: lint, type check, testes
├── pyproject.toml
├── BUILD_EXECUTABLE.md          # Guia para gerar o .exe
├── LICENSE
└── README.md
```

---

## Desenvolvimento

### Rodar testes

```bash
uv sync --extra dev

uv run pytest tests/ -v

# Com cobertura
uv run pytest tests/ --cov=cartomet_br --cov-report=term-missing
```

### Linting e type check

```bash
uv run ruff check cartomet_br/
uv run ruff format --check cartomet_br/
uv run mypy cartomet_br/ --ignore-missing-imports
```

### Gerar executável (PyInstaller)

```bash
uv run pyinstaller cartomet_br.spec
```

### CI/CD

O projeto usa **GitHub Actions** para rodar lint, type check e testes em cada push e pull request (Ubuntu + Windows).

---

## Contribuindo

1. Faça um fork do projeto
2. Crie uma branch para sua feature (`git checkout -b feature/nova-funcionalidade`)
3. Rode os testes (`uv run pytest tests/ -v`)
4. Commit suas mudanças (`git commit -m 'Adiciona nova funcionalidade'`)
5. Push para a branch (`git push origin feature/nova-funcionalidade`)
6. Abra um Pull Request

---

## Autores

<p align="center">
  <strong>Desenvolvedor & Idealizador</strong>
</p>

<p align="center">
  <strong>Elivaldo C. Rocha</strong>
</p>

<p align="center">
  Bacharel em Meteorologia — FAMET/UFPA<br/>
  Mestre em Gestão de Riscos e Desastres na Amazônia — PPGGRD/UFPA<br/>
  MBA em Geotecnologias e Análise de Dados Espaciais<br/>
  Esp. Georreferenciamento, Geoprocessamento e Sensoriamento Remoto<br/>
  Esp. Ciência de Dados Geográficos<br/>
  Esp. Agrometeorologia e Climatologia<br/>
  Analista e Desenvolvedor de Sistemas
</p>

<p align="center">
  <a href="mailto:carvalhovaldo09@gmail.com">
    <img src="https://img.shields.io/badge/Email-carvalhovaldo09%40gmail.com-D14836?style=for-the-badge&logo=gmail&logoColor=white" alt="Email"/>
  </a>
  <a href="https://github.com/ElivaldoRocha">
    <img src="https://img.shields.io/badge/GitHub-ElivaldoRocha-181717?style=for-the-badge&logo=github" alt="GitHub"/>
  </a>
</p>

---

<p align="center">
  <strong>Idealizador</strong>
</p>

<p align="center">
  <strong>Prof. Dr. Everaldo Barreiros de Souza</strong>
</p>

<p align="center">
  Professor Titular — Instituto de Geociências (IG/UFPA)<br/>
  Doutor em Meteorologia — USP/IAG<br/>
  Mestre em Meteorologia — INPE/CPTEC<br/>
  Docente Permanente do PPGCA — Mestrado e Doutorado (UFPA/MPEG/EMBRAPA)<br/>
  Bolsista PQ-2 Produtividade em Pesquisa — CNPq<br/>
  Líder do Grupo Modelagem Climática Aplicada às Ciências Ambientais da Amazônia<br/>
  +135 artigos científicos publicados
</p>

<p align="center">
  <a href="https://scholar.google.com.br/citations?user=erkpC_4AAAAJ&hl=en">
    <img src="https://img.shields.io/badge/Google_Scholar-4285F4?style=for-the-badge&logo=google-scholar&logoColor=white" alt="Google Scholar"/>
  </a>
  <a href="https://www.webofscience.com/wos/author/record/1299836">
    <img src="https://img.shields.io/badge/Web_of_Science-5C5C5C?style=for-the-badge" alt="Web of Science"/>
  </a>
</p>

---

## Instituições

<p align="center">
  <img src="cartomet_br/assets/Logos_UFPA_IG_FAMET_PPGGRD.png" alt="Logos Institucionais" width="200"/>
</p>

<p align="center">
  <strong>Universidade Federal do Pará (UFPA)</strong><br/><br/>
  <strong>IG</strong> — Instituto de Geociências<br/>
  <strong>FAMET</strong> — Faculdade de Meteorologia<br/>
  <strong>PPGGRD</strong> — Programa de Pós-Graduação em Gestão de Riscos e Desastres na Amazônia<br/>
</p>

---

## Licença

Este projeto está sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.

---

## Agradecimentos

- **ECMWF** — Por disponibilizar dados meteorológicos gratuitamente através do Open Data
- **NOAA** — Pelas imagens de satélite GOES-East disponíveis no AWS S3
- **NASA/NOAA** — Pelo dataset MUR SST de alta resolução disponível via ERDDAP
- **CPTEC/INPE** — Pela metodologia operacional de detecção de ZCAS (Escobar, 2019)
- **Comunidade Python** — Pelas excelentes bibliotecas científicas (xarray, cartopy, matplotlib, MetPy)
- **UFPA, IG, FAMET e PPGGRD** — Pela formação acadêmica e inspiração

---

## Como Citar

Se você utilizar o CartoMet BR em pesquisas, trabalhos acadêmicos ou publicações científicas, por favor cite:

**ABNT**

> ROCHA, Elivaldo C.; SOUZA, Everaldo Barreiros de. CartoMet BR: A Python Package for Meteorological Cartography in Brazil (v2.2). Versão 2.2.0. Zenodo, 2026. DOI: 10.5281/zenodo.19157369. Disponível em: <https://doi.org/10.5281/zenodo.19157369>.

**APA**

> Rocha, E. C., & Souza, E. B. de. (2026). *CartoMet BR: A Python Package for Meteorological Cartography in Brazil (v2.2)* (2.2.0). Zenodo. <https://doi.org/10.5281/zenodo.19157369>

**BibTeX**

```bibtex
@software{rocha_2026_cartomet,
  author       = {Rocha, Elivaldo Carvalho and
                  Souza, Everaldo Barreiros de},
  title        = {{CartoMet BR: A Python Package for Meteorological
                   Cartography in Brazil (v2.2)}},
  version      = {2.2.0},
  publisher    = {Zenodo},
  year         = {2026},
  month        = mar,
  doi          = {10.5281/zenodo.19157369},
  url          = {https://doi.org/10.5281/zenodo.19157369}
}
```

**IEEE**

> E. C. Rocha and E. B. de Souza, "CartoMet BR: A Python Package for Meteorological Cartography in Brazil (v2.2)," version 2.2.0, Zenodo, 2026. doi: 10.5281/zenodo.19157369.

---

## Referências

- **Escobar, G. C. J. (2019)** — Zona de Convergência do Atlântico Sul (ZCAS): critério de detecção para uso em centros operacionais de previsão de tempo. *Nota Técnica*, CPTEC/INPE.
- **Petterssen, S. (1936)** — Contribution to the theory of frontogenesis. *Geofysiske Publikasjoner*, 11(6), 1-27.

---

<p align="center">
  <strong>CartoMet BR v2.2</strong> — Análise sinótica completa para sala de aula e profissionais da meteorologia.
</p>

<p align="center">
  Desenvolvido com dedicação por Elivaldo C. Rocha
</p>

<p align="center">
  <sub>Dados: ECMWF Open Data (CC BY 4.0) | Satélite: NOAA GOES-East (Domínio Público) | TSM: NASA/NOAA MUR SST (Domínio Público)</sub>
</p>
