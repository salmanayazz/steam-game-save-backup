from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SGSB_", env_file=".env")

    data_dir: Path = Path("data")
    sync_interval_minutes: int = 120  # 0 disables the background schedule
    request_delay_min: float = 3.0  # between Steam store page requests
    request_delay_max: float = 5.0
    download_delay_min: float = 0.5
    download_delay_max: float = 1.5
    sync_poll_seconds: int = 2  # how often the browser refreshes sync progress
    login_poll_seconds: int = 2  # how often the browser checks the QR login

    @property
    def session_file(self) -> Path:
        return self.data_dir / "session.json"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'app.db'}"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


settings = Settings()
