import argparse

from studio.server import launch

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Music Analyzer")
    parser.add_argument("--port", type=int, default=0,
                        help="Local port. The default, 0, selects an available port.")
    parser.add_argument("--no-browser", action="store_true", help="Start without opening a browser window.")
    options = parser.parse_args()
    launch(options.port, not options.no_browser)
