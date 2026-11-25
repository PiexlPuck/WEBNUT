"""Application configuration loaded from environment variables."""

from pathlib import Path
from typing import Literal
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for WEBNUT."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    webnut_role: Literal["main", "repeater"] = "main"
    web_port: int = 8765
    nut_port: int = 3493
    webnut_internal_nut_port: int = 3494
    nut_host: str = "127.0.0.1"
    webnut_data_dir: Path = Path("/app/data")
    webnut_nut_dir: Path = Path("/etc/nut")
    webnut_static_dir: Path = Path("/app/static")
    webnut_supervisor_conf: Path = Path("/etc/supervisord.conf")
    webnut_ha_component_dir: Path = Path("/app/ha_integration/webnut")
    webnut_github_repo: str = "PiexlPuck/WEBNUT"
    webnut_update_check_interval_sec: int = 21600
    webnut_version_file: Path | None = None
    webnut_main_url: str = ""
    webnut_join_token: str = ""
    webnut_node_name: str = ""
    webnut_repeater_public_url: str = ""
    webnut_repeater_heartbeat_timeout_sec: int = 30
    tz: str = "UTC"

    @field_validator("webnut_role", mode="before")
    @classmethod
    def _normalize_role(cls, v: object) -> str:
        s = str(v or "main").strip().lower()
        return s

    @property
    def db_path(self) -> Path:
        return self.webnut_data_dir / "app.db"


settings = Settings()
