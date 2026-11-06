from pydantic import BaseModel
import os
class Settings(BaseModel):
    MQTT_BROKER: str = os.getenv("MQTT_BROKER", "localhost")
    MQTT_PORT: int = int(os.getenv("MQTT_PORT", "1883"))
    POSTGRES_DSN: str = os.getenv("POSTGRES_DSN", "postgresql+psycopg2://hdt:hdt@localhost:5432/hdt")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    SLO_A_MIN: float = float(os.getenv("SLO_A_MIN", "0.90"))
    SLO_L_MAX: float = float(os.getenv("SLO_L_MAX", "120"))
    SLO_E_MAX: float = float(os.getenv("SLO_E_MAX", "250"))
    W_L: float = float(os.getenv("W_L", "0.4"))
    W_E: float = float(os.getenv("W_E", "0.3"))
    W_D: float = float(os.getenv("W_D", "0.3"))
    ETA: float = float(os.getenv("ETA", "0.1"))
    ALPHA: float = float(os.getenv("ALPHA", "0.4"))
    BETA: float = float(os.getenv("BETA", "0.6"))
    DELTA_T_MAX: float = float(os.getenv("DELTA_T_MAX", "10.0"))
settings = Settings()
