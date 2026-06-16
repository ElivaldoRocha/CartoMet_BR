"""Módulo data — Download e processamento de dados meteorológicos."""

from cartomet_br.data.ecmwf import (
    SynopticData,
    download_ecmwf,
    load_synoptic_data,
)

__all__ = [
    "download_ecmwf",
    "load_synoptic_data",
    "SynopticData",
]
