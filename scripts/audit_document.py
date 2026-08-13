#!/usr/bin/env python3
import sys
from pathlib import Path

# Add root directory to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner.prose_audit import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
