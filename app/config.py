from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./energyguard.db"
    api_key: str = ""
    cors_origins: str = "*"

    # Isolation Forest
    model_path: str = "models/isolation_forest.joblib"
    min_train_samples: int = 200
    retrain_every: int = 500
    train_window: int = 20000
    contamination: float = 0.02
    n_estimators: int = 200

    # Alertas
    notify_on_anomaly: bool = True
    anomaly_alert_cooldown_s: int = 900
    offline_after_s: int = 120

    # Notificacoes (deixe vazio para desligar)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
