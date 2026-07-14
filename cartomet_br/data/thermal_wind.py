"""Vento térmico e advecção (veering/backing) num ponto — módulo PURO.

Sem Qt/matplotlib: recebe a coluna vertical de vento de um ponto (níveis de
pressão + ``u``/``v``) e devolve, para uma camada escolhida (base → topo), o
**vento de cada nível** (para a hodógrafa) e o **vetor de vento térmico** de cada
subcamada, classificando a **advecção térmica** por subcamada.

Física. O vento térmico de uma camada é a diferença dos ventos geostróficos entre
o topo e a base; aqui aproximamos pelos ventos do modelo (cisalhamento), que é o
vento térmico sob balanço geostrófico. A advecção sai do **giro do vento com a
altura** (veering/backing), medido pelo produto vetorial de ventos consecutivos::

    cross_z = u_b * v_t - v_b * u_t          (b = base, t = topo da subcamada)

que é proporcional a ``V_médio × V_T`` — logo, à advecção térmica geostrófica. O
sinal depende do **hemisfério** (a regra se inverte por causa de Coriolis):

* **Hemisfério Sul (lat < 0):** giro anti-horário com a altura (*backing*,
  ``cross_z > 0``) → **advecção quente**; horário (*veering*, ``cross_z < 0``) →
  **advecção fria**.
* **Hemisfério Norte (lat > 0):** o oposto.

Referência: relação do vento térmico (Holton & Hakim); regra veering/backing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Níveis-padrão da hodógrafa (hPa), da base (maior pressão) ao topo (menor).
# Densos o bastante para uma curva rica (como numa hodógrafa clássica), usando
# só níveis presentes no perfil do modelo (subconjunto de PL_LEVELS).
STANDARD_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300]

# Abaixo deste seno do ângulo entre ventos consecutivos, o giro é desprezível
# (ventos ~paralelos/antiparalelos ou calmos) → advecção "neutra".
_SIN_NEUTRAL = math.sin(math.radians(1.0))  # ~1°


@dataclass(frozen=True)
class ThermalWindLayer:
    """Vento térmico de uma subcamada (entre dois níveis consecutivos)."""

    p_bottom: int  # hPa (maior pressão — base da subcamada)
    p_top: int  # hPa (menor pressão — topo da subcamada)
    u_thermal: float  # m/s — V_T = V(topo) - V(base)
    v_thermal: float
    advection: str  # "warm" | "cold" | "neutral"


@dataclass
class ThermalWindResult:
    """Hodógrafa do ponto + vetores térmicos por subcamada."""

    latitude: float
    longitude: float
    levels: list[int] = field(default_factory=list)  # base→topo (pressão decrescente)
    u: list[float] = field(default_factory=list)  # vento u por nível (m/s), alinhado a levels
    v: list[float] = field(default_factory=list)  # vento v por nível (m/s)
    layers: list[ThermalWindLayer] = field(default_factory=list)  # subcamadas consecutivas
    net_u_thermal: float = 0.0  # V(topo) - V(base) da camada inteira
    net_v_thermal: float = 0.0
    net_advection: str = "neutral"


def classify_advection(u_b: float, v_b: float, u_t: float, v_t: float, latitude: float) -> str:
    """Advecção térmica pelo giro do vento base→topo, ciente do hemisfério.

    ``"warm"``/``"cold"``/``"neutral"``. Neutro quando o giro é desprezível
    (ventos ~paralelos ou calmos), evitando cor espúria.
    """
    cross_z = u_b * v_t - v_b * u_t
    denom = math.hypot(u_b, v_b) * math.hypot(u_t, v_t)
    if denom < 1e-6 or abs(cross_z) < _SIN_NEUTRAL * denom:
        return "neutral"
    # HS: cross_z > 0 (backing/anti-horário) = quente. HN: inverte.
    warm = (cross_z > 0.0) if latitude < 0 else (cross_z < 0.0)
    return "warm" if warm else "cold"


def compute_thermal_wind(
    pressures,
    u,
    v,
    base_p: float,
    top_p: float,
    latitude: float,
    longitude: float = 0.0,
    *,
    levels: list[int] | None = None,
) -> ThermalWindResult:
    """Monta a hodógrafa e os vetores térmicos da camada ``base_p → top_p``.

    Seleciona os níveis-padrão presentes no perfil dentro de ``[top_p, base_p]``
    (com ``u``/``v`` válidos), ordenados da base ao topo, e calcula o vetor
    térmico e a advecção de cada subcamada + os valores líquidos da camada.

    ``ValueError`` se sobrarem menos de 2 níveis válidos (hodógrafa impossível).
    """
    pressures = np.asarray(pressures, dtype=float)
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    std = list(levels) if levels is not None else STANDARD_LEVELS

    lo, hi = (top_p, base_p) if base_p >= top_p else (base_p, top_p)

    # Índice do nível-padrão no perfil (match exato com tolerância), se u/v válidos.
    avail: dict[int, int] = {}
    for i, p in enumerate(pressures):
        avail[int(round(float(p)))] = i

    sel_levels: list[int] = []
    sel_u: list[float] = []
    sel_v: list[float] = []
    for lv in sorted(std, reverse=True):  # 1000, 850, 700, 500, 300 → base→topo
        if not (lo <= lv <= hi) or lv not in avail:
            continue
        idx = avail[lv]
        uu, vv = float(u[idx]), float(v[idx])
        if math.isnan(uu) or math.isnan(vv):  # nível sob o relevo / ausente
            continue
        sel_levels.append(lv)
        sel_u.append(uu)
        sel_v.append(vv)

    if len(sel_levels) < 2:
        raise ValueError(
            "Camada com menos de 2 níveis válidos para a hodógrafa "
            f"(base={base_p:.0f} hPa, topo={top_p:.0f} hPa)."
        )

    layers: list[ThermalWindLayer] = []
    for i in range(len(sel_levels) - 1):
        u_b, v_b = sel_u[i], sel_v[i]
        u_t, v_t = sel_u[i + 1], sel_v[i + 1]
        layers.append(
            ThermalWindLayer(
                p_bottom=sel_levels[i],
                p_top=sel_levels[i + 1],
                u_thermal=u_t - u_b,
                v_thermal=v_t - v_b,
                advection=classify_advection(u_b, v_b, u_t, v_t, latitude),
            )
        )

    net_u = sel_u[-1] - sel_u[0]
    net_v = sel_v[-1] - sel_v[0]
    net_adv = classify_advection(sel_u[0], sel_v[0], sel_u[-1], sel_v[-1], latitude)

    return ThermalWindResult(
        latitude=float(latitude),
        longitude=float(longitude),
        levels=sel_levels,
        u=sel_u,
        v=sel_v,
        layers=layers,
        net_u_thermal=net_u,
        net_v_thermal=net_v,
        net_advection=net_adv,
    )
