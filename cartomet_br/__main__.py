"""
Interface de linha de comando do CartoMet_BR.

Uso:
    python -m cartomet_br gui                    # Interface gráfica PyQt6
    python -m cartomet_br synoptic [--step N] [--extent LON_MIN LAT_MIN LON_MAX LAT_MAX]
    python -m cartomet_br interactive [--with-synoptic] [--step N]
    python -m cartomet_br --help
"""

import argparse
import sys

from cartomet_br import __version__
from cartomet_br.charts.interactive import run_interactive
from cartomet_br.charts.synoptic import create_synoptic_chart
from cartomet_br.core.config import EXTENT_AMSUL, EXTENT_BRASIL, Config


def main():
    parser = argparse.ArgumentParser(
        prog="cartomet_br",
        description="CartoMet_BR — Cartografia Meteorológica para o Brasil",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  # Gera carta sinótica (análise)
  python -m cartomet_br synoptic
  
  # Gera carta sinótica (previsão de 24h)
  python -m cartomet_br synoptic --step 24
  
  # Gera carta sinótica para região específica
  python -m cartomet_br synoptic --extent -75 -35 -30 6
  
  # Abre ferramenta interativa (mapa em branco)
  python -m cartomet_br interactive
  
  # Abre ferramenta interativa sobre carta sinótica
  python -m cartomet_br interactive --with-synoptic
        """,
    )

    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"CartoMet_BR {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # ─── Comando: gui ───────────────────────────────────────────────────
    gui_parser = subparsers.add_parser(
        "gui",
        help="Abre interface gráfica PyQt6 (recomendado)",
    )

    # ─── Comando: synoptic ───────────────────────────────────────────────
    synoptic_parser = subparsers.add_parser(
        "synoptic",
        help="Gera carta sinótica automática",
    )
    synoptic_parser.add_argument(
        "--step", "-s",
        type=int,
        default=0,
        help="Passo de previsão em horas (0=análise, 24, 48, etc.)",
    )
    synoptic_parser.add_argument(
        "--extent", "-e",
        type=float,
        nargs=4,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
        default=None,
        help="Extensão geográfica [lon_min, lat_min, lon_max, lat_max]",
    )
    synoptic_parser.add_argument(
        "--brasil",
        action="store_true",
        help="Usa extensão predefinida para o Brasil",
    )
    synoptic_parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Resolução da imagem (default: 200)",
    )
    synoptic_parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Nome do arquivo de saída",
    )
    synoptic_parser.add_argument(
        "--no-show",
        action="store_true",
        help="Não exibe a figura (apenas salva)",
    )

    # ─── Comando: interactive ────────────────────────────────────────────
    interactive_parser = subparsers.add_parser(
        "interactive",
        help="Abre ferramenta interativa para desenhar frentes",
    )
    interactive_parser.add_argument(
        "--with-synoptic", "-w",
        action="store_true",
        help="Usa carta sinótica como fundo",
    )
    interactive_parser.add_argument(
        "--step", "-s",
        type=int,
        default=0,
        help="Passo de previsão (se usar fundo sinótico)",
    )
    interactive_parser.add_argument(
        "--extent", "-e",
        type=float,
        nargs=4,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
        default=None,
        help="Extensão geográfica",
    )
    interactive_parser.add_argument(
        "--brasil",
        action="store_true",
        help="Usa extensão predefinida para o Brasil",
    )

    # Parse args
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Configura extent
    extent = EXTENT_AMSUL
    if hasattr(args, "brasil") and args.brasil:
        extent = EXTENT_BRASIL
    if hasattr(args, "extent") and args.extent is not None:
        extent = args.extent

    config = Config(extent=extent)
    if hasattr(args, "dpi"):
        config.dpi = args.dpi

    # Executa comando
    if args.command == "gui":
        from cartomet_br.gui import run_gui
        run_gui()

    elif args.command == "synoptic":
        create_synoptic_chart(
            config=config,
            step=args.step,
            output_filename=args.output,
            show=not args.no_show,
        )

    elif args.command == "interactive":
        run_interactive(
            config=config,
            use_synoptic_background=args.with_synoptic,
            step=args.step,
        )


if __name__ == "__main__":
    main()
