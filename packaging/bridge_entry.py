"""Entry point of the frozen Python bridge shipped inside release packages.

PyInstaller freezes this script (`python3 scripts/dev.py bridge-build`); it runs exactly what
`python -m control_tv.bridge` runs in development, so the packaged application and the
development build share one bridge implementation.
"""

from control_tv.bridge import main

if __name__ == "__main__":
    main()
