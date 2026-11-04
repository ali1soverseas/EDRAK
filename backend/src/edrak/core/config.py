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
    # The workspace that owned the pre-loaded demo data (data/internal and the handbook) in v1.
    # Every other workspace starts with an empty knowledge base.
    DEMO_WORKSPACE_ID: str = "ws-default"

    # Application database. Unset means data/edrak.db; tests point it at a temporary file.
    EDRAK_DB_PATH: Optional[Path] = None
    # Uploaded originals live here, one folder per workspace.
    UPLOADS_PATH: Path = Field(default=Path("data/uploads"))
    # LangGraph checkpoints for paused runs (the second human checkpoint survives a restart).
    ORCHESTRATION_CHECKPOINT_PATH: Path = Field(default=Path("data/orchestration_checkpoints.db"))
    MAX_UPLOAD_MB: int = 25

    # Guardrails: the scope check asks a model whether a question is a business question.
    # Off in tests and when no model is configured; the pattern checks always run.
    # Read saved decision tags into the decision analysis. Tags are stored either way.
    PERSONALIZATION_ENABLED: bool = False
    # How many relevant past decisions are scored. The candidate pool is at least 50.
    PERSONALIZATION_HISTORY_K: int = 20
    # Minimum cosine similarity for a past decision to count.
    PERSONALIZATION_SIMILARITY_MIN: float = 0.55
    # Personalization stays off unless this many decisions pass the threshold, and unless
    # one aspect has a score of at least this value or is carried by at least this many decisions.
    PERSONALIZATION_ASPECT_BAR: int = 2
    GUARDRAIL_SCOPE_CHECK: bool = True
    # Hook A: JEV semantic second layer in check_query (2 Nouls, ~1 credit per
    # novel query). Off by default; the deterministic checks always run.
    JEVSTATION_API_KEY: Optional[str] = None
    JEV_GUARD_ENABLED: bool = False
    JEV_HIGH: float = 0.85
    JEV_REVIEW: float = 0.5
    JEV_TIMEOUT_S: float = 8.0
    JEV_MAX_CALLS_PER_DAY: int = 40
    # Evaluation after each run: a model judges a sample of findings (faithfulness, relevance).
    EVALUATION_JUDGE: bool = True
    # The metrics agent runs after verification and fills the figures the planner asked for.
    # Off leaves the verified worker results as they are. The brief is still built.
    METRICS_GATHERING_ENABLED: bool = True

    # Company profile import: source text goes to the model in one labeled, budgeted block.
    # The model is OLLAMA_MODEL. Reasoning effort is passed to the client as given ("" turns the option off).
    PROFILE_IMPORT_CONTEXT_CHARS: int = 48000
    PROFILE_IMPORT_MIN_CHARS: int = 400
    PROFILE_IMPORT_REASONING: str = "low"
    PROFILE_IMPORT_MAX_LIST_ITEMS: int = 20
    PROFILE_IMPORT_MAX_ITEM_CHARS: int = 200
    PROFILE_IMPORT_MAX_NOTES_CHARS: int = 600
    # Importing from a website: how much of the site is read and how.
    PROFILE_IMPORT_MAX_PAGES: int = 8  # including the homepage
    PROFILE_IMPORT_FETCH_TIMEOUT_S: float = 10.0
    PROFILE_IMPORT_MAX_BYTES: int = 2_000_000  # per response
    PROFILE_IMPORT_CONCURRENCY: int = 3
    PROFILE_IMPORT_MAX_REDIRECTS: int = 5
    # A homepage with less main text than this is retried in a headless browser, when one is installed.
    PROFILE_IMPORT_RENDER_MIN_CHARS: int = 300
    # Importing from a PDF: upload limits, and when a page counts as a scan that needs OCR.
    PROFILE_IMPORT_PDF_MAX_MB: int = 20
    PROFILE_IMPORT_PDF_MAX_PAGES: int = 60
    PROFILE_IMPORT_SCANNED_MIN_CHARS: int = 50
    # Tesseract language codes joined with "+". Both need their language data installed.
    PROFILE_IMPORT_OCR_LANGUAGE: str = "ara+eng"
    PROFILE_IMPORT_OCR_DPI: int = 300

    # Company research crawl: how deeply the company's own website is read. Fetching reuses the
    # website import's timeout, redirect limit and user agent.
    COMPANY_RESEARCH_CRAWL_MAX_PAGES: int = 40  # including the homepage
    COMPANY_RESEARCH_CRAWL_MAX_DEPTH: int = 3  # links followed from the homepage
    COMPANY_RESEARCH_CRAWL_TIME_BUDGET_S: float = 180.0  # the whole crawl
    COMPANY_RESEARCH_CRAWL_CONCURRENCY: int = 3
    COMPANY_RESEARCH_CRAWL_HOST_DELAY_S: float = 1.0  # between requests to the same host
    COMPANY_RESEARCH_CRAWL_MAX_BYTES: int = 2_000_000  # per response
    # A headless browser reads pages whose static HTML has less main text than this, or that look
    # client-rendered or lazy-loaded. It scrolls, clicks "load more" and expands tabs first.
    COMPANY_RESEARCH_CRAWL_DYNAMIC: bool = True
    COMPANY_RESEARCH_CRAWL_DYNAMIC_MIN_CHARS: int = 300
    COMPANY_RESEARCH_CRAWL_MAX_DYNAMIC_PAGES: int = 15
    COMPANY_RESEARCH_CRAWL_MAX_SCROLLS: int = 8
    COMPANY_RESEARCH_CRAWL_SCROLL_PAUSE_MS: int = 600
    COMPANY_RESEARCH_CRAWL_MAX_CLICKS: int = 5  # "load more" style buttons, per page
    COMPANY_RESEARCH_CRAWL_MAX_EXPANDS: int = 20  # tabs and accordions, per page

    # Company research PDFs: downloaded from the company's own pages, read, and deleted. The host
    # delay and the fetch timeout are the crawl's.
    COMPANY_RESEARCH_PDF_MAX_PDFS: int = 8  # read per run
    COMPANY_RESEARCH_PDF_MAX_MB: int = 20  # per file
    COMPANY_RESEARCH_PDF_MAX_PAGES: int = 60  # read per file; later pages are left out
    COMPANY_RESEARCH_PDF_MAX_ATTEMPTS: int = 24  # downloads tried per run, whatever the outcome
    COMPANY_RESEARCH_PDF_CONTEXT_CHARS: int = 48000  # text kept per file; a longer file is cut to the best pages
    COMPANY_RESEARCH_PDF_TIME_BUDGET_S: float = 240.0  # downloading and reading all files; the last one is given up when it runs out

    # Company research extraction: facts read from crawled pages and PDF pages by the model, then
    # checked against the sources. The model, its reasoning level and the repair retry are the
    # profile import's.
    COMPANY_RESEARCH_EXTRACT_BATCH_CHARS: int = 24000  # source text per model call; a document is never split
    COMPANY_RESEARCH_EXTRACT_MAX_BATCHES: int = 30  # model calls per run
    # How closely a quote must match its source, as a share of matching text (0 to 1). Used for
    # OCR text only; every other source must contain the quote exactly, after normalization.
    COMPANY_RESEARCH_EXTRACT_OCR_TOLERANCE: float = 0.9

    # Company research run: the graph that crawls, reads PDFs, extracts facts and fills gaps.
    COMPANY_RESEARCH_RUN_TIME_BUDGET_S: float = 900.0  # no new round of pages starts after this
    COMPANY_RESEARCH_RUN_MAX_S: float = 3600.0  # the run is stopped and failed after this, whatever it is doing
    # Categories a run tries to fill with extra rounds on the company's own site, comma separated.
    COMPANY_RESEARCH_IMPORTANT_CATEGORIES: str = "leadership,ownership_and_funding,financials,locations,history"
    COMPANY_RESEARCH_GAP_MAX_ROUNDS: int = 2  # extra crawl rounds, never more than 2
    COMPANY_RESEARCH_GAP_MAX_TARGETS: int = 4  # pages read per extra round
    # Only the company's own social bios are read, and only for categories still empty. Third party
    # sources (Serper search, then the top results) are off unless this is on.
    COMPANY_RESEARCH_THIRD_PARTY: bool = False
    COMPANY_RESEARCH_THIRD_PARTY_MAX_SEARCHES: int = 4
    COMPANY_RESEARCH_THIRD_PARTY_RESULTS_PER_SEARCH: int = 3  # top results read for each search
    COMPANY_RESEARCH_THIRD_PARTY_MAX_PAGES: int = 6  # pages read in all, across searches

    # Observability & Monitoring (Langfuse)
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "http://localhost:3000"
    LANGFUSE_RELEASE: str = "edrak-2.0.0"

    # Sessions
    SESSION_DAYS: int = 30
    # Send the session cookie over HTTPS only. Defaults to on outside development.
    COOKIE_SECURE: Optional[bool] = None

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

    def _absolute(self, path: Path) -> Path:
        return path if path.is_absolute() else (self.BASE_DIR / path).resolve()

    def get_db_path(self) -> Path:
        return self._absolute(self.EDRAK_DB_PATH or Path("data/edrak.db"))

    def get_uploads_path(self) -> Path:
        return self._absolute(self.UPLOADS_PATH)

    def get_data_path(self) -> Path:
        return self._absolute(Path("data"))

    def get_checkpoint_path(self) -> Path:
        return self._absolute(self.ORCHESTRATION_CHECKPOINT_PATH)

    @property
    def cookie_secure(self) -> bool:
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.EDRAK_ENV.lower() not in ("development", "dev", "test", "local")

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

