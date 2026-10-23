"""Top-level CLI entry point for `python -m src.factory <status|next|release>`."""

import sys
from src.orchestrator.cli import main

if __name__ == "__main__":
    sys.exit(main())
