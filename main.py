"""Console entry point with user input:  python main.py  [file.c] [-v]"""

import sys

from compiler.cli import run

if __name__ == "__main__":
    sys.exit(run())
