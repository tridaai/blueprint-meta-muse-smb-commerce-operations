#!/usr/bin/env python3
"""The CLI surface. Usage: python scripts/cli.py [generate|facts|ledger|verify|model-calls]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_commerce.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
