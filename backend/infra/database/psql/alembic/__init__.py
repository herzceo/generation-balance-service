from pathlib import Path

ALEMBIC_CONFIG = (Path(__file__).parent / "alembic.ini").as_posix()

__all__ = ("ALEMBIC_CONFIG",)
