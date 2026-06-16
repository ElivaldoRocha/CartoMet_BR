"""
Módulo symbols — Simbologias meteorológicas.

Inclui todas as classes de frentes e efeitos meteorológicos,
além do dicionário MODOS para uso interativo.
"""

from cartomet_br.symbols.base import (
    _FrenteBase,
    draw_filled,
    draw_open,
    make_arrowhead,
    make_circle,
    make_semicircle,
    make_triangle,
)
from cartomet_br.symbols.effects import (
    CavadoEffect,
    CorrenteDeJato,
    Crista,
    LinhaInstabilidade,
    LinhaSeca,
    ZCASEffect,
    ZCITEffect,
)
from cartomet_br.symbols.fronts import (
    FrenteEstacionaria,
    FrenteFria,
    FrenteOclusa,
    FrenteQuente,
    Frontogenese,
    Frontolise,
)
from cartomet_br.symbols.point_symbols import (
    draw_hurricane,
    draw_tropical_storm,
    draw_vortex,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  TABELA DE MODOS (para interface interativa)
# ═══════════════════════════════════════════════════════════════════════════════

MODOS = {
    # ── Frentes (traçar linha) ──
    "1": {
        "nome": "Frente Fria",
        "cor": "#1a6faf",
        "tem_flip": True,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [FrenteFria(flip=flip)],
    },
    "2": {
        "nome": "Frente Quente",
        "cor": "#c0392b",
        "tem_flip": True,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [FrenteQuente(flip=flip)],
    },
    "3": {
        "nome": "Frente Estacionária",
        "cor": "#6a0dad",
        "tem_flip": True,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [FrenteEstacionaria(flip=flip)],
    },
    "4": {
        "nome": "Frente Oclusa",
        "cor": "#8e44ad",
        "tem_flip": True,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [FrenteOclusa(flip=flip)],
    },
    # ── Efeitos (traçar linha) ──
    "5": {
        "nome": "ZCAS",
        "cor": "#008000",
        "tem_flip": False,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [ZCASEffect()],
    },
    "6": {
        "nome": "ZCIT",
        "cor": "darkorange",
        "tem_flip": False,
        "ponto": False,
        "tem_intensidade": True,
        "efeito": lambda flip=False, intensity=1: [ZCITEffect(intensity=intensity)],
    },
    "7": {
        "nome": "Cavado",
        "cor": "saddlebrown",
        "tem_flip": False,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [CavadoEffect()],
    },
    "8": {
        "nome": "Crista (RidgeAxis)",
        "cor": "#005500",
        "tem_flip": False,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [Crista()],
    },
    "9": {
        "nome": "Linha de Instabilidade",
        "cor": "#8B0000",
        "tem_flip": False,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [LinhaInstabilidade()],
    },
    "0": {
        "nome": "Linha Seca (Dryline)",
        "cor": "#b5651d",
        "tem_flip": False,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [LinhaSeca()],
    },
    # ── Frontogênese / Frontólise (traçar linha) ──
    "e": {
        "nome": "Frontogênese",
        "cor": "#1a6faf",
        "tem_flip": True,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [Frontogenese(flip=flip)],
    },
    "d": {
        "nome": "Frontólise",
        "cor": "#1a6faf",
        "tem_flip": True,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [Frontolise(flip=flip)],
    },
    # ── Corrente de Jato (traçar linha) ──
    "j": {
        "nome": "Corrente de Jato",
        "cor": "#0033AA",
        "tem_flip": False,
        "ponto": False,
        "efeito": lambda flip=False, intensity=1: [CorrenteDeJato()],
    },
    # ── Símbolos pontuais (clique único) ──
    "a": {
        "nome": "Alta Pressão (A)",
        "cor": "#0066CC",
        "tem_flip": False,
        "ponto": True,
        "label": "A",
        "fontsize": 22,
    },
    "b": {
        "nome": "Baixa Pressão (B)",
        "cor": "#CC0000",
        "tem_flip": False,
        "ponto": True,
        "label": "B",
        "fontsize": 22,
    },
    "h": {
        "nome": "Furacão",
        "cor": "#CC0000",
        "tem_flip": False,
        "ponto": True,
        "draw_func": draw_hurricane,
    },
    "t": {
        "nome": "Tempestade Tropical",
        "cor": "#CC0000",
        "tem_flip": False,
        "ponto": True,
        "draw_func": draw_tropical_storm,
    },
    "v": {
        "nome": "Vórtice",
        "cor": "#CC0000",
        "tem_flip": False,
        "ponto": True,
        "draw_func": draw_vortex,
    },
}


__all__ = [
    # Base
    "_FrenteBase",
    "make_triangle",
    "make_semicircle",
    "make_circle",
    "make_arrowhead",
    "draw_filled",
    "draw_open",
    # Frentes
    "FrenteFria",
    "FrenteQuente",
    "FrenteEstacionaria",
    "FrenteOclusa",
    "Frontogenese",
    "Frontolise",
    # Efeitos
    "ZCASEffect",
    "ZCITEffect",
    "CavadoEffect",
    "Crista",
    "LinhaInstabilidade",
    "LinhaSeca",
    "CorrenteDeJato",
    # Pontuais
    "draw_hurricane",
    "draw_tropical_storm",
    "draw_vortex",
    # Modos
    "MODOS",
]
