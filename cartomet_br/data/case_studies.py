"""Biblioteca de estudos de caso ERA5 — catálogo curado (lógica pura, sem GUI).

Cada caso descreve um evento sinótico marcante do Brasil com a janela temporal,
a região (extent na **ordem do Config**: ``[lon_min, lat_min, lon_max,
lat_max]``), a receita de camadas ERA5 que o reconstrói na mesa e o "porquê"
sinótico (material didático). A GUI consome o catálogo pelo diálogo
``CaseStudyDialog`` e por uma fila serializada de downloads na janela
principal (clone do preset Diagnóstico Baroclínico, cache-first).

Contratos com o restante do app (os testes travam):

- ``LayerRequest`` espelha EXATAMENTE o payload de 7 campos do sinal
  ``add_era5_layer_requested`` do ``ERA5Panel`` (+ ``visible``, usado só pela
  fila para deixar camadas de apoio empilhadas porém desligadas);
- todo período de camada tem ≤ ``ERA5_LONG_PERIOD_DAYS`` dias — a fila roda
  sem disparar o diálogo de confirmação de período longo;
- os modos de agregação pertencem ao perfil físico da variável (a mesma régua
  que o ``ERA5Panel`` aplica na criação manual);
- a ordem das camadas é a ordem de empilhamento (primeira = base, visível).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════════
#  ESTRUTURAS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CaseLayerSpec:
    """Uma camada da receita do caso; datas/hora ``None`` herdam as do caso.

    ``nota`` é a meia-linha didática exibida no diálogo (o que a camada mostra
    e por quê) — obrigatória, para o aluno saber o que está empilhando.
    """

    var_key: str
    level: int = 0  # 0 = superfície; >0 = nível de pressão (hPa)
    agg: str = "hora"
    hour: int | None = None
    date_start: str | None = None
    date_end: str | None = None
    visible: bool = True  # False = entra na pilha DESLIGADA (camada de apoio)
    nota: str = ""


@dataclass(frozen=True)
class LayerRequest:
    """Payload resolvido — espelho do sinal ``add_era5_layer_requested``."""

    var_key: str
    date_start: str
    date_end: str
    hour: int
    level: int
    agg: str
    thresh: float
    visible: bool


@dataclass(frozen=True)
class CaseStudy:
    """Um estudo de caso curado: evento, região, receita de camadas e didática."""

    key: str
    nome: str
    quando: str  # rótulo humano do período ("27–28/03/2004")
    date_start: str  # ISO — período de referência do caso
    date_end: str
    hour: int  # hora sinótica UTC dos campos instantâneos
    extent: tuple[int, int, int, int]  # ordem do Config (spinboxes são inteiros)
    resumo: str  # 2–3 linhas: o que aconteceu
    porque: str  # o "porquê" sinótico — material didático do diálogo
    referencia: str
    layers: tuple[CaseLayerSpec, ...] = field(default_factory=tuple)

    def layer_requests(self) -> list[LayerRequest]:
        """Resolve as camadas nos payloads exatos do sinal do ``ERA5Panel``.

        Regras (as mesmas do ``ERA5Panel._emit_request``): no modo instantâneo
        (``"hora"``) a data final é FORÇADA igual à inicial; nos agregados o
        período da camada (ou do caso) vale inteiro. ``thresh`` é sempre 0.0 —
        o catálogo não usa índices de dias quentes/onda de calor.
        """
        requests: list[LayerRequest] = []
        for spec in self.layers:
            d0 = spec.date_start or self.date_start
            d1 = d0 if spec.agg == "hora" else (spec.date_end or self.date_end)
            requests.append(
                LayerRequest(
                    var_key=spec.var_key,
                    date_start=d0,
                    date_end=d1,
                    hour=self.hour if spec.hour is None else spec.hour,
                    level=int(spec.level),
                    agg=spec.agg,
                    thresh=0.0,
                    visible=spec.visible,
                )
            )
        return requests


# ═══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO CURADO (lista aprovada pelo usuário — decisão de conteúdo)
# ═══════════════════════════════════════════════════════════════════════════════

CASE_STUDIES: tuple[CaseStudy, ...] = (
    CaseStudy(
        key="catarina_2004",
        nome="Furacão Catarina",
        quando="27–28/03/2004",
        date_start="2004-03-27",
        date_end="2004-03-28",
        hour=18,
        extent=(-65, -45, -20, -15),
        resumo=(
            "O único furacão documentado do Atlântico Sul: um ciclone que fez "
            "transição extratropical→tropical sob bloqueio e atingiu o litoral "
            "sul de SC na madrugada de 28/03 com ventos de categoria 1."
        ),
        porque=(
            "O dipolo de bloqueio em Z500 desacelerou e isolou o sistema sobre o "
            "mar, num ambiente raro de cisalhamento vertical fraco. Os fluxos de "
            "calor oceânicos sustentaram convecção junto ao centro, que adquiriu "
            "núcleo quente — a transição tropical.\n\n"
            "Repare na TSM média da semana: 24–25 °C, ABAIXO do limiar clássico "
            "de 26,5 °C dos furacões. A lição: o que importa é o desequilíbrio "
            "termodinâmico ar–mar (ar muito frio em altitude sobre mar "
            "relativamente morno), não um número mágico de TSM."
        ),
        referencia="McTaggart-Cowan et al. (2006, MWR); Pezza e Simmonds (2005, GRL).",
        layers=(
            CaseLayerSpec(
                var_key="era5_mslp",
                agg="hora",
                nota="PNMM às 18 UTC de 27/03 — o núcleo fechado se aproximando de SC.",
            ),
            CaseLayerSpec(
                var_key="era5pl_gh",
                level=500,
                agg="hora",
                visible=False,
                nota="Z500 — o bloqueio que prendeu e guiou o sistema para oeste.",
            ),
            CaseLayerSpec(
                var_key="era5_wind10m",
                agg="hora",
                visible=False,
                nota="Vento a 10 m — a circulação fechada com força de furacão.",
            ),
            CaseLayerSpec(
                var_key="era5_sst",
                agg="media",
                date_start="2004-03-20",
                date_end="2004-03-27",
                visible=False,
                nota="TSM média da semana — furacão SEM os 26,5 °C 'clássicos'.",
            ),
        ),
    ),
    CaseStudy(
        key="bomba_2020",
        nome="Ciclone-bomba no Sul",
        quando="30/06–01/07/2020",
        date_start="2020-06-30",
        date_end="2020-07-01",
        hour=18,
        extent=(-70, -45, -30, -15),
        resumo=(
            "Ciclogênese explosiva na costa de SC/RS: aprofundamento na ordem do "
            "critério de 'bomba' (~24 hPa/24 h ajustado pela latitude), rajadas "
            "acima de 100 km/h, destelhamentos e mortes em Santa Catarina."
        ),
        porque=(
            "A receita da ciclogênese explosiva: forte baroclinia em baixos "
            "níveis (veja o contraste térmico em T850), acoplamento com o jato "
            "em 250 hPa (divergência em altitude evacuando massa da coluna) e o "
            "oceano alimentando umidade.\n\n"
            "Na carta, o dano não está no centro da baixa — está no GRADIENTE de "
            "pressão. Compare o aperto das isóbaras com o rastro da rajada "
            "máxima do episódio: é o gradiente que venta."
        ),
        referencia="Critério de ciclogênese explosiva: Sanders e Gyakum (1980, MWR).",
        layers=(
            CaseLayerSpec(
                var_key="era5_mslp",
                agg="hora",
                nota="PNMM às 18 UTC de 30/06 — a baixa em aprofundamento explosivo.",
            ),
            CaseLayerSpec(
                var_key="era5_gust",
                agg="maxima",
                visible=False,
                nota="Rajada máxima de 30/06–01/07 — o rastro do vento destrutivo.",
            ),
            CaseLayerSpec(
                var_key="era5pl_wind",
                level=250,
                agg="hora",
                visible=False,
                nota="Vento em 250 hPa — o jato acoplado à ciclogênese.",
            ),
            CaseLayerSpec(
                var_key="era5pl_t",
                level=850,
                agg="hora",
                visible=False,
                nota="T850 — a baroclinia (contraste térmico) que armou a bomba.",
            ),
        ),
    ),
    CaseStudy(
        key="zcas_petropolis_2022",
        nome="ZCAS e a tragédia de Petrópolis",
        quando="15/02/2022",
        date_start="2022-02-15",
        date_end="2022-02-15",
        hour=18,
        extent=(-60, -30, -30, -10),
        resumo=(
            "A tarde mais letal de Petrópolis (RJ): ~260 mm em poucas horas — "
            "acima da média climatológica do mês inteiro e o maior volume desde "
            "o início das medições locais (1932) —, com centenas de vítimas."
        ),
        porque=(
            "O ambiente é o clássico de ZCAS: um corredor de umidade NW–SE "
            "(água precipitável alta) alimentado pelo fluxo de 850 hPa vindo da "
            "Amazônia, com ascenso persistente na banda (ω<0 em 500 hPa). Sobre "
            "a serra, a forçante local ancorou a convecção.\n\n"
            "Honestidade de escala: na grade do ERA5 (~31 km) o TOTAL pontual de "
            "Petrópolis NÃO aparece — o pixel dilui o extremo. A reanálise "
            "mostra o AMBIENTE que tornou o extremo possível; o valor local, só "
            "o pluviômetro. Use o caso para discutir escala com os alunos."
        ),
        referencia="Zonas de convergência subtropicais: Kodama (1992, JMSJ); INMET/CEMADEN (2022).",
        layers=(
            CaseLayerSpec(
                var_key="era5_precip",
                agg="soma",
                nota="Chuva total do dia 15/02 — a banda da ZCAS no acumulado.",
            ),
            CaseLayerSpec(
                var_key="era5_tcwv",
                agg="hora",
                visible=False,
                nota="Água precipitável às 18 UTC — o corredor de umidade NW–SE.",
            ),
            CaseLayerSpec(
                var_key="era5pl_wind",
                level=850,
                agg="hora",
                visible=False,
                nota="Vento em 850 hPa — o fluxo que abastece a banda.",
            ),
            CaseLayerSpec(
                var_key="era5pl_w",
                level=500,
                agg="hora",
                visible=False,
                nota="ω em 500 hPa — o ramo ascendente persistente da ZCAS.",
            ),
        ),
    ),
    CaseStudy(
        key="friagem_2021",
        nome="Friagem e neve históricas",
        quando="28–30/07/2021",
        date_start="2021-07-28",
        date_end="2021-07-30",
        hour=12,
        extent=(-75, -35, -35, 0),
        resumo=(
            "Uma das incursões polares mais intensas em décadas: nevou em "
            "dezenas de municípios do Sul na noite de 28–29/07, geou do CO ao "
            "SE e a friagem avançou pelo sul da Amazônia."
        ),
        porque=(
            "Atrás da frente fria, um anticiclone polar migratório intenso "
            "injetou ar frio profundo continente adentro — acompanhe a língua "
            "fria em T850 cruzando latitudes baixas. Com céu limpo, ar seco e "
            "vento calmo à noite, o resfriamento radiativo fez o resto: geada "
            "ampla.\n\n"
            "Na Amazônia, o mesmo surge é a 'friagem': o previsor a rastreia "
            "pelo jato de sul em 925 hPa contornando os Andes. A mínima "
            "absoluta do episódio desenha o alcance geográfico do evento."
        ),
        referencia="Surges frios na América do Sul: Marengo et al. (1997, MWR); INMET (2021).",
        layers=(
            CaseLayerSpec(
                var_key="era5_tmin",
                agg="minima",
                nota="Mínima absoluta de 28–30/07 — o mapa do alcance da geada.",
            ),
            CaseLayerSpec(
                var_key="era5_mslp",
                agg="hora",
                date_start="2021-07-29",
                visible=False,
                nota="PNMM às 12 UTC de 29/07 — a alta polar no auge da incursão.",
            ),
            CaseLayerSpec(
                var_key="era5pl_t",
                level=850,
                agg="hora",
                date_start="2021-07-29",
                visible=False,
                nota="T850 — a língua de ar frio em altitude (geada advectiva).",
            ),
            CaseLayerSpec(
                var_key="era5pl_wind",
                level=925,
                agg="hora",
                date_start="2021-07-28",
                visible=False,
                nota="Vento em 925 hPa — o surge de sul canalizado pelos Andes.",
            ),
        ),
    ),
    CaseStudy(
        key="cheia_amazonas_2021",
        nome="Cheia recorde do Amazonas",
        quando="maio/2021 (pico da cheia em junho)",
        date_start="2021-05-01",
        date_end="2021-05-31",
        hour=12,
        extent=(-80, -15, -45, 5),
        resumo=(
            "A maior cheia já medida no rio Negro: 30,02 m em Manaus "
            "(16/06/2021), recorde em ~119 anos de régua. O 'evento' não é um "
            "dia — é a estação chuvosa reforçada sob La Niña."
        ),
        porque=(
            "Sob La Niña, a convecção se intensifica sobre o norte/noroeste da "
            "bacia, e meses de chuva acima do normal antecedem a crista da "
            "cheia — a hidrologia atrasa a meteorologia em semanas. O mapa de "
            "maio mostra a cauda da estação chuvosa que alimentou o pico de "
            "junho.\n\n"
            "Didática do caso: aqui a 'análise' é CLIMATOLÓGICA — campos "
            "somados/médios de um mês, não um instante sinótico. Repare no "
            "transporte médio de umidade em 850 hPa convergindo para a bacia. "
            "É o uso das agregações da reanálise, impossível com modelo de "
            "previsão."
        ),
        referencia="Espinoza et al. (2022); boletins de cheia SGB/CPRM (2021).",
        layers=(
            CaseLayerSpec(
                var_key="era5_precip",
                agg="soma",
                nota="Chuva total de maio/2021 — o abastecimento da cheia.",
            ),
            CaseLayerSpec(
                var_key="era5_tcwv",
                agg="media",
                visible=False,
                nota="Água precipitável média do mês — a umidade disponível.",
            ),
            CaseLayerSpec(
                var_key="era5pl_wind",
                level=850,
                agg="media",
                visible=False,
                nota="Vento médio em 850 hPa — o transporte de umidade p/ a bacia.",
            ),
        ),
    ),
)


def get_case(key: str) -> CaseStudy | None:
    """Caso pelo ``key`` estável, ou ``None`` se não existir."""
    for case in CASE_STUDIES:
        if case.key == key:
            return case
    return None
