from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "СмартСклад"
    database_url: str = "postgresql+psycopg://smartsklad:smartsklad@localhost:5432/smartsklad"

    model_config = {"env_file": ".env"}


settings = Settings()