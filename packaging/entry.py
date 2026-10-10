"""Start-up script for the packaged app (PyInstaller runs this file)."""

from contextbridge.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
