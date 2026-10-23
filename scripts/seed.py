"""Reset only this Compose lab's link data and seed the versioned read corpus."""

import argparse
import os

from scripts.benchmark import ROOT, reset_and_seed
from scripts.locking import exclusive_operation


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    with exclusive_operation():
        if (ROOT / "artifacts/active-drill.json").exists():
            raise RuntimeError("Heal the active failure drill before resetting data")
        reset_and_seed(os.environ.copy())
    print("Reset lab links, counters and cache; seeded the versioned read corpus.")


if __name__ == "__main__":
    main()
