"""Rebuild only the cross-source dashboard status after rebase on latest main.

The source monitor initially writes status before publishing. Another workflow
can publish a failure record while this run waits on the shared GitHub lock.
After rebase, always reproject status from the latest main source evidence.
"""
from pathlib import Path
from . import dashboard as D, state as S


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    D.write_status(root, [], S.load_state(root))


if __name__ == "__main__":
    main()
