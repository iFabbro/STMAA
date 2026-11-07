#!/usr/bin/env python3
"""STMAA terminal dashboard entry point.

The dashboard is a read-only observer. Its UI shell has no broker or trading
integration; data providers are introduced in separately reviewed stages.
"""


def main() -> None:
    from src.dashboard import STMAADashboard

    STMAADashboard().run()


if __name__ == "__main__":
    main()
