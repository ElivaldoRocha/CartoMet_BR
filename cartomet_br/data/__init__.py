"""Módulo data — Download e processamento de dados meteorológicos."""

from cartomet_br.data.ecmwf import (
    download_ecmwf,
    load_synoptic_data,
    SynopticData,
)

__all__ = [
    "download_ecmwf",
    "load_synoptic_data",
    "SynopticData",
]
