#!/usr/bin/env python3
"""Run the demo: generate the dataset and walk the operating picture."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_commerce.demo import main

if __name__ == "__main__":
    raise SystemExit(main())
