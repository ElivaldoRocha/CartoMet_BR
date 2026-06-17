#!/usr/bin/env python
"""
CartoMet BR — Ponto de entrada para executável PyInstaller

Configura caminhos e variáveis de ambiente necessárias para o executável funcionar.
O diretório de dados é configurado pelo usuário na primeira execução via GUI.
"""

import io
import logging
import os
import sys
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
#  CORREÇÃO CRÍTICA: Modo Windowed do PyInstaller
# ═══════════════════════════════════════════════════════════════════════════════
# Quando PyInstaller usa console=False (modo GUI puro), sys.stdout e sys.stderr
# são None. Bibliotecas que tentam usar print() ou progress bars (como ecmwf-opendata)
# falham com "'NoneType' object has no attribute 'write'".
# Solução: criar "terminais falsos" em memória.

if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()


logger = logging.getLogger(__name__)


def setup_environment():
    """Configura ambiente para executável PyInstaller."""

    if getattr(sys, "frozen", False):
        # Executando como executável PyInstaller
        # _MEIPASS é o diretório temporário onde PyInstaller extrai os arquivos
        BASE_DIR = Path(sys._MEIPASS)

        logger.info("Executável inicializado")
        logger.debug("BASE_DIR: %s", BASE_DIR)

        # === Certificados SSL ===
        # CRÍTICO para requests/urllib3 funcionar (download ECMWF)
        cacert_found = False
        cacert_paths = [
            BASE_DIR / "certifi" / "cacert.pem",
            BASE_DIR / "cacert.pem",
        ]
        for cacert in cacert_paths:
            if cacert.exists():
                os.environ["SSL_CERT_FILE"] = str(cacert)
                os.environ["REQUESTS_CA_BUNDLE"] = str(cacert)
                os.environ["CURL_CA_BUNDLE"] = str(cacert)
                cacert_found = True
                logger.info("SSL Cert: %s", cacert)
                break

        if not cacert_found:
            logger.warning("Certificado SSL não encontrado!")

        # === PyProj / PROJ ===
        proj_paths = [
            BASE_DIR / "proj",
            BASE_DIR / "pyproj" / "proj_dir" / "share" / "proj",
            BASE_DIR / "share" / "proj",
        ]
        for proj_dir in proj_paths:
            if proj_dir.exists():
                os.environ["PROJ_LIB"] = str(proj_dir)
                os.environ["PROJ_DATA"] = str(proj_dir)
                logger.info("PROJ_LIB: %s", proj_dir)
                break

        # === Cartopy ===
        cartopy_paths = [
            BASE_DIR / "cartopy",
            BASE_DIR / "cartopy" / "data",
        ]
        for cartopy_dir in cartopy_paths:
            if cartopy_dir.exists():
                os.environ["CARTOPY_DIR"] = str(
                    cartopy_dir.parent if cartopy_dir.name == "data" else cartopy_dir
                )
                logger.info("CARTOPY_DIR: %s", os.environ["CARTOPY_DIR"])
                break

        # === eccodes ===
        eccodes_paths = [
            BASE_DIR / "eccodes",
            BASE_DIR / "Library" / "bin",
        ]
        for eccodes_dir in eccodes_paths:
            if eccodes_dir.exists():
                os.environ["ECCODES_DIR"] = str(eccodes_dir)
                # Adiciona ao PATH para encontrar DLLs
                os.environ["PATH"] = str(eccodes_dir) + os.pathsep + os.environ.get("PATH", "")
                logger.info("ECCODES_DIR: %s", eccodes_dir)
                break

        # NÃO definimos CARTOMET_DATA_DIR aqui!
        # O usuário escolhe o diretório na primeira execução via GUI.
        # Isso evita problemas de permissão em pastas protegidas do Windows.

        logger.info("Ambiente configurado com sucesso")

    else:
        # Executando como script Python normal
        logger.info("Modo desenvolvimento")


def main():
    """Função principal."""
    # Configura logging antes de tudo
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

    try:
        setup_environment()

        # Autoteste de empacotamento: importa/exercita tudo que as features usam,
        # DENTRO do binário congelado (com os env vars de PROJ/eccodes/SSL já
        # configurados acima). Pega "módulo não embarcado" antes do usuário.
        if "--selftest" in sys.argv:
            from cartomet_br._selftest import run_selftest

            sys.exit(run_selftest())

        from cartomet_br.gui import run_gui

        run_gui()

    except Exception as e:
        # Em caso de erro crítico, mostra mensagem amigável
        import traceback

        error_msg = f"Erro ao iniciar CartoMet BR:\n\n{e}\n\n{traceback.format_exc()}"
        logger.critical(error_msg)

        # Tenta mostrar diálogo de erro
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox

            QApplication(sys.argv)
            QMessageBox.critical(None, "Erro Crítico", error_msg)
        except (ImportError, RuntimeError):
            pass

        sys.exit(1)


if __name__ == "__main__":
    main()
