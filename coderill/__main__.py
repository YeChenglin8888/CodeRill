"""Allow ``python -m coderill``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
