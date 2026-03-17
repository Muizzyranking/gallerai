from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "GallerAI"
    description: str = "Event photo sharing with face recognition"
    debug: bool = False
    app_env: str = "development"
    secret_key: str = "key-**"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60  # 1 hr

    # PSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "gallerai"
    postgres_user: str = "gallerai"
    postgres_password: str = "gallerai"

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # mongo
    mongo_host: str = "localhost"
    mongo_port: int = 27017
    mongo_db: str = "gallerai"

    @property
    def mongo_url(self) -> str:
        return f"mongodb://{self.mongo_host}:{self.mongo_port}/{self.mongo_db}"

    # redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    def get_redis_url(self, db: int | None = None) -> str:
        db = db if db else self.redis_db
        return f"redis://{self.redis_host}:{self.redis_port}/{db}"

    @property
    def redis_url(self) -> str:
        return self.get_redis_url()

    @property
    def celery_broker_url(self) -> str:
        return self.get_redis_url()

    @property
    def celery_result_backend(self) -> str:
        return self.get_redis_url(1)

    # storage
    storage_backend: str = "local"
    local_storage_path: str = "./storage"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
