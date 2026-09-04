from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JURIS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://juris:juris@localhost:5432/juris"
    temas_path: Path = Path("config/temas.yaml")
    backfill_inicio: date = date(2020, 1, 1)
    watermark_margem_dias: int = 2
    max_paginas_por_rodada: int = 50
    req_intervalo_segundos: float = 2.0
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alerta_cooldown_horas: int = 6
    watchdog_limite_horas: int = 36


settings = Settings()
