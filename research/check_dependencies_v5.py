"""Check that the Windows pilot's current interpreter has its required modules.

This is an external Python file rather than `python -c` to avoid quoting
loss in Windows PowerShell 5.1 when passing embedded string literals.
"""

from importlib.util import find_spec
import sys

REQUIRED = (
    "fastapi", "httpx", "uvicorn", "pydantic", "sqlalchemy", "psycopg2",
    "redis", "aiomqtt", "dotenv", "requests", "numpy", "pandas",
    "matplotlib",
)


def main() -> int:
    missing = [module for module in REQUIRED if find_spec(module) is None]
    if missing:
        print("FALTAN dependencias en .venv: " + ", ".join(missing), flush=True)
        return 1
    print("OK: dependencias presentes en .venv", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
