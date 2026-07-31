from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://scouter:scouter@localhost:5432/cuny_scouter"
    discord_webhook_url: str = ""
    discord_ops_webhook_url: str = ""
    secret_key: str = "change-me-in-production"

    # Discord OAuth
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/discord/callback"

    # Scraper
    poll_interval_seconds: int = 600
    institution: str = "BAR01"
    institution_name: str = "Baruch College | "
    term_code: str = "1269"
    term_name: str = "2026 Fall Term"
    subject: str = "CMIS"
    subject_name: str = "Computer Information Systems"
    scraper_user_agent: str = "baruch-class-tracker/0.1 (contact: stefanekfernandez@gmail.com)"


settings = Settings()
