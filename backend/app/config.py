"""Application configuration loaded from environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for WEBNUT."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    web_port: int = 8765
    nut_port: int = 3493
    nut_host: str = "127.0.0.1"
    webnut_data_dir: Path = Path("/app/data")
    webnut_nut_dir: Path = Path("/etc/nut")
    webnut_static_dir: Path = Path("/app/static")
    webnut_supervisor_conf: Path = Path("/etc/supervisord.conf")
    tz: str = "UTC"

    @property
    def db_path(self) -> Path:
        return self.webnut_data_dir / "app.db"


settings = Settings()
