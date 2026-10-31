from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SGSB_", env_file=".env")

    data_dir: Path = Path("data")

    @property
    def session_file(self) -> Path:
        return self.data_dir / "session.json"


settings = Settings()
