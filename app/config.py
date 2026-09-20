from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "СмартСклад"
    database_url: str = "postgresql+psycopg://smartsklad:smartsklad@localhost:5432/smartsklad"
    session_ttl_days: int = 7
    admin_username: str = ""
    admin_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    base_url: str = "http://127.0.0.1:8000"

    model_config = {"env_file": ".env"}


settings = Settings()