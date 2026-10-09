"""EDRAK core configuration settings using Pydantic Settings."""

from pathlib import Path
from typing import Optional
from pydantic import AliasChoices, Field
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
    # Every component talks to the native Ollama client, so the OLLAMA_* names match
    # customer_trends' own settings. The LLM_* names stay accepted as environment
    # aliases so an existing .env keeps working.
    OLLAMA_MODEL: str = Field(
        default="gpt-oss:120b",
        validation_alias=AliasChoices("OLLAMA_MODEL", "LLM_MODEL"),
    )
    # The /v1 suffix is an OpenAI-compatible shim path. It still applies to the raw
    # httpx client; the native client uses the bare host and overrides this in llm.py.
    OLLAMA_BASE_URL: Optional[str] = Field(
        default="https://ollama.com/v1",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "LLM_BASE_URL"),
    )
    OLLAMA_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OLLAMA_API_KEY", "LLM_API_KEY"),
    )
    OLLAMA_TEMPERATURE: float = Field(
        default=0.2,
        validation_alias=AliasChoices("OLLAMA_TEMPERATURE", "LLM_TEMPERATURE"),
    )

    # Orchestration control-plane limits
    # 1 not 2: a replan now re-dispatches only the workers whose findings
    # contradicted, but it still re-runs their research. One attempt is enough
    # to resolve a contradiction, and a second buys very little.
    MAX_REPLANS: int = 1
    MAX_TASK_ATTEMPTS: int = 3

    # Embedding Settings (Local Hugging Face Static Embeddings)
    EMBEDDING_PROVIDER: str = "huggingface"  # 'huggingface', 'onnx', 'sentence-transformers', 'local_fast'
    EMBEDDING_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"  # or 'BAAI/bge-small-en-v1.5'
    EMBEDDING_DIMENSION: int = 384
    EMBEDDING_ALLOW_FALLBACK: bool = False
    RETRIEVAL_MIN_SCORE: float = 0.30

    # Internal Intelligence Worker Reranking & Retrieval Settings
    INTERNAL_RERANK_SCORE_CUTOFF: float = 0.58
    INTERNAL_RERANK_MAX_ITEMS: int = 12
    INTERNAL_RERANK_MIN_ITEMS: int = 5

    # Storage & Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent.parent
    VECTOR_STORE_PATH: Path = Field(default=Path("data/vector_store"))
    INTERNAL_DATA_PATH: Path = Field(default=Path("data/internal"))
    RAW_HANDBOOK_PATH: Path = Field(default=Path("data/handbook/raw"))
    CLEANED_HANDBOOK_PATH: Path = Field(default=Path("data/handbook/cleaned"))
    CHUNKED_HANDBOOK_PATH: Path = Field(default=Path("data/handbook/chunked"))
    ARTIFACTS_PATH: Path = Field(default=Path("artifacts"))
    CHROMA_COLLECTION_NAME: str = "edrak_internal_knowledge"

    # Web scraping
    SCRAPE_TEXT_CHARS: int = 2000

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

