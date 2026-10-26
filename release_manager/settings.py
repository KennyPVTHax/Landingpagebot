from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./release_manager.db"
    internal_api_token: str = Field(min_length=32)
    key_pepper: str = Field(min_length=32)
    bot_token: str = ""
    admin_telegram_ids: str = ""
    api_base_url: str = "http://127.0.0.1:8087"

    @property
    def admins(self):
        return {int(v.strip()) for v in self.admin_telegram_ids.split(",") if v.strip()}
