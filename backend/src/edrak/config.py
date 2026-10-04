from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "ollama"
    llm_model: str = "llama3.2"
    scrape_text_chars: int = 2000
    artifacts_path: str = "artifacts"


settings = Settings()
