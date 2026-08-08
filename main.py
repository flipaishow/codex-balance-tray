"""Entry point for Codex Balance Tray."""

from codex_tray.app import TrayApplication
from codex_tray.startup import ensure_startup_enabled


def main() -> None:
    ensure_startup_enabled()
    TrayApplication().run()


if __name__ == "__main__":
    main()
