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

# Episode import rules (see docs/import-rules.md). Constants, not environment
# settings: changing one changes the rules, so it belongs in code review.
KNOWN_ROBOTS = ("arm-01", "arm-02", "arm-03", "mobile-01", "humanoid-01")
MAX_DURATION_SECONDS = 3600  # episodes are short clips; longer means a data error
MAX_IMPORT_FILE_BYTES = 20 * 1024 * 1024  # uploads only; the CLI has no limit
