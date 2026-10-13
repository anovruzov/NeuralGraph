"""The package's command line (``docs/collective/E2E-INGEST.md`` section 6, M2, M12):

    python -m research.e2e_ingest run --out DIR [--size 200] [--mode process] [--suites DIR]
    python -m research.e2e_ingest measure --out DIR [--sizes 200,600,2000] [--suites DIR]
    python -m research.e2e_ingest check REPORT [--recorded]
    python -m research.e2e_ingest suites --out DIR [--group core|lab] [--suite NAME ...] [--verify]

``run`` is the end-to-end job (``harness.py``), ``measure`` the measure job (``measure.py``), ``check`` re-reads a
report of either job and recomputes its aggregates (M12), and ``suites`` runs the seven unit suites with junit
evidence (M2). The modules are imported on demand so that ``suites`` loads none of the fabric, the sites or the
simulator.
"""
from __future__ import annotations

import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    commands = ("run", "measure", "check", "suites")
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if args else 2
    command, rest = args[0], args[1:]
    if command not in commands:
        print(f"error: the command is one of {', '.join(commands)}", file=sys.stderr)
        return 2
    if command == "suites":
        from .suites import main as suites_main
        return suites_main(rest)
    if command == "measure":
        from .measure import main as measure_main
        return measure_main(rest)
    from .harness import main as harness_main
    return harness_main([command, *rest])


if __name__ == "__main__":
    sys.exit(main())
