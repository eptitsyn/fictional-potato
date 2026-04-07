from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    SECRET_KEY: str = "dev-secret-key-change-in-production-64chars-padding-here-xx"
    ENCRYPTION_SALT: str = "dev-salt-16chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://codereviewer:codereviewer_secret@localhost:5432/codereviewer"
    )

    # First admin (bootstrapped on startup)
    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_USERNAME: str = "admin"
    FIRST_ADMIN_PASSWORD: str = "changeme123"


settings = Settings()
