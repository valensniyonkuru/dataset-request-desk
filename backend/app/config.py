from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App configuration, read only from environment variables.

    DATABASE_URL and SECRET_KEY have no default, so the app refuses to start
    if they are missing instead of running with an unsafe value.
    """

    DATABASE_URL: str
    SECRET_KEY: str
    ENV: str = "development"


settings = Settings()
