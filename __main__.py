import argparse

from studio.server import launch

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Music Analyzer")
    parser.add_argument("--port", type=int, default=0,
                        help="Místní port. Výchozí 0 zvolí volný port.")
    parser.add_argument("--no-browser", action="store_true", help="Neotvírat okno prohlížeče.")
    options = parser.parse_args()
    launch(options.port, not options.no_browser)
