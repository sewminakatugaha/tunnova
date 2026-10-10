"""Run with: python3 -m tunnova"""
import sys
from . import __version__


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--version":
        print(__version__)
        return 0
    if len(sys.argv) > 1:
        print("Usage: python3 -m tunnova [--version]", file=sys.stderr)
        return 2
    try:
        from .gui import run
    except (ImportError, ValueError) as exc:
        print("GTK4/PyGObject is required to run the GUI.", file=sys.stderr)
        print("On Linux Mint / Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0", file=sys.stderr)
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
