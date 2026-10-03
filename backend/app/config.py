from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App configuration, read only from environment variables.

    DATABASE_URL and SECRET_KEY have no default, so the app refuses to start
    if they are missing instead of running with an unsafe value.
    """

    DATABASE_URL: str
    SECRET_KEY: str
    ENV: str = "development"
    # Folder holding users.json. The backend image copies the repo's seed/ here.
    SEED_DIR: str = "/app/seed"
    # Login: a signed JWT in an HttpOnly cookie, valid for this many minutes.
    ACCESS_TOKEN_MINUTES: int = 60
    COOKIE_NAME: str = "desk_session"


settings = Settings()
