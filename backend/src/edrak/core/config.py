"""EDRAK core configuration settings using Pydantic Settings."""

from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    EDRAK_ENV: str = "development"
    EDRAK_DEBUG: bool = True

    # LLM Settings (Ollama Cloud / Local OSS)
    LLM_PROVIDER: str = "ollama"  # e.g., 'ollama', 'openai', 'anthropic', 'google', 'mock'
    LLM_MODEL: str = "gpt-oss:120b"
    LLM_BASE_URL: Optional[str] = "http://localhost:11434/v1"
    LLM_API_KEY: Optional[str] = None
    LLM_TEMPERATURE: float = 0.2

    # Embedding Settings (Local Hugging Face Static Embeddings)
    EMBEDDING_PROVIDER: str = "huggingface"  # 'huggingface', 'onnx', 'sentence-transformers', 'local_fast'
    EMBEDDING_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"  # or 'BAAI/bge-small-en-v1.5'
    EMBEDDING_DIMENSION: int = 384

    # Storage & Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent.parent
    VECTOR_STORE_PATH: Path = Field(default=Path("data/vector_store"))
    INTERNAL_DATA_PATH: Path = Field(default=Path("data/internal"))
    RAW_HANDBOOK_PATH: Path = Field(default=Path("data/handbook/raw"))
    CLEANED_HANDBOOK_PATH: Path = Field(default=Path("data/handbook/cleaned"))
    CHUNKED_HANDBOOK_PATH: Path = Field(default=Path("data/handbook/chunked"))
    ARTIFACTS_PATH: Path = Field(default=Path("artifacts"))
    CHROMA_COLLECTION_NAME: str = "edrak_internal_knowledge"

    # API / Server
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 8000

    def get_absolute_vector_store_path(self) -> Path:
        """Returns the absolute path to vector store directory."""
        if self.VECTOR_STORE_PATH.is_absolute():
            return self.VECTOR_STORE_PATH
        return (self.BASE_DIR / self.VECTOR_STORE_PATH).resolve()

    def get_absolute_internal_data_path(self) -> Path:
        """Returns the absolute path to internal data directory."""
        if self.INTERNAL_DATA_PATH.is_absolute():
            return self.INTERNAL_DATA_PATH
        return (self.BASE_DIR / self.INTERNAL_DATA_PATH).resolve()

    def get_absolute_raw_handbook_path(self) -> Path:
        """Returns the absolute path to raw scraped handbook pages."""
        if self.RAW_HANDBOOK_PATH.is_absolute():
            return self.RAW_HANDBOOK_PATH
        return (self.BASE_DIR / self.RAW_HANDBOOK_PATH).resolve()

    def get_absolute_cleaned_handbook_path(self) -> Path:
        """Returns the absolute path to cleaned handbook markdown files."""
        if self.CLEANED_HANDBOOK_PATH.is_absolute():
            return self.CLEANED_HANDBOOK_PATH
        return (self.BASE_DIR / self.CLEANED_HANDBOOK_PATH).resolve()

    def get_absolute_chunked_handbook_path(self) -> Path:
        """Returns the absolute path to chunked handbook documents."""
        if self.CHUNKED_HANDBOOK_PATH.is_absolute():
            return self.CHUNKED_HANDBOOK_PATH
        return (self.BASE_DIR / self.CHUNKED_HANDBOOK_PATH).resolve()


settings = Settings()

