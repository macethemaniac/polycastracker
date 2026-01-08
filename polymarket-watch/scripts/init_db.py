from __future__ import annotations

from pathlib import Path

from alembic.config import main as alembic_main


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    alembic_ini = repo_root / "alembic.ini"
    alembic_main(["-c", str(alembic_ini), "upgrade", "head"])


if __name__ == "__main__":
    main()
