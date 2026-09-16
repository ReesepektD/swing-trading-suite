"""Entry point: ``python3 -m video_sorter``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
