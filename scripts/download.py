#!/usr/bin/env python3
"""Fetch or check the files this repo is built from, as sources.yaml lists them.

    python scripts/download.py --check            # what is present; no network
    python scripts/download.py --accept-terms     # fetch what can be fetched

ANES distributes its data under the terms on its Data Center page
(https://electionstudies.org/data-center/): research and statistical use only,
no use of respondents' identities, and citation. electionstudies.org blocks
scripted downloads, so every file is fetched by hand in a browser; --check prints
the steps for each and verifies what's there.

Uses the PublicDataToolkit library (pdt.download); `make download` sets PYTHONPATH.
"""

import sys
from pathlib import Path

try:
    from pdt.download import main
except ImportError:
    sys.exit("Needs the PublicDataToolkit library: pip install -e ../PublicDataToolkit, "
             "or run through `make download`.")

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    sys.exit(main([str(ROOT), *sys.argv[1:]]))
