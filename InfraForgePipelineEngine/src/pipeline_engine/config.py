from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8100
    log_level: str = "info"
    default_step_timeout: int = 300
    default_pipeline_timeout: int = 3600
    max_heal_attempts: int = 5
    run_store_ttl: int = 3600

    model_config = {"env_prefix": "PE_"}


settings = Settings()
