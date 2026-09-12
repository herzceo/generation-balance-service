from backend.internal.dto import StructDTO


class DatabaseConfig(StructDTO):
    POSTGRES_HOST: str
    POSTGRES_PORT: str
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASS: str
    POSTGRES_DRIVER: str = "psycopg"

    POSTGRES_POOL_SIZE: int = 100
    POSTGRES_MAX_OVERFLOW: int = 20

    def get_postgres_url(self) -> str:
        return f"postgresql+{self.POSTGRES_DRIVER}://{self.POSTGRES_USER}:{self.POSTGRES_PASS}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
