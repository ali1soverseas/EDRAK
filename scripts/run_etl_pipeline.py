"""Master ETL Pipeline Runner for EDRAK GitLab Handbook & Internal Knowledge.

Runs all or selected stages of the internal knowledge pipeline:
1. Fetch: Scrape GitLab Handbook pages -> data/handbook/raw/
2. Clean: HTML extraction & Markdown formatting -> data/handbook/cleaned/
3. Chunk: Heading & sliding window chunking -> data/handbook/chunked/
4. Ingest: Upsert into ChromaDB with Hugging Face embeddings -> data/vector_store/
"""

import argparse
import logging
from pathlib import Path
import sys

# Ensure repo_root and backend/src are on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
scripts_dir = repo_root / "scripts"

for p in [str(repo_root), str(backend_src), str(scripts_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from fetch_gitlab_handbook import HandbookCrawler
from clean_gitlab_handbook import HandbookCleaner
from chunk_gitlab_handbook import HandbookChunker
from ingest_internal_data import main as run_ingest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("etl_pipeline")


def run_pipeline(
    fetch: bool,
    clean: bool,
    chunk: bool,
    ingest: bool,
    start_url: str,
    max_pages: int,
    reset_db: bool,
):
    logger.info("==================================================================")
    logger.info("Starting EDRAK Internal Knowledge ETL Pipeline")
    logger.info("==================================================================")

    # Step 1: Fetch
    if fetch:
        logger.info("\n>>> STAGE 1: Fetching GitLab Handbook Pages...")
        crawler = HandbookCrawler(start_url=start_url, max_pages=max_pages)
        crawler.crawl()

    # Step 2: Clean
    if clean:
        logger.info("\n>>> STAGE 2: Cleaning Raw HTML to Markdown...")
        cleaner = HandbookCleaner()
        cleaner.process_all()

    # Step 3: Chunk
    if chunk:
        logger.info("\n>>> STAGE 3: Semantic Chunking...")
        chunker = HandbookChunker()
        chunker.process_all()

    # Step 4: Ingest
    if ingest:
        logger.info("\n>>> STAGE 4: ChromaDB Vector Store Ingestion...")
        # Prepare sys.argv for ingest main
        sys.argv = ["ingest_internal_data.py"]
        if reset_db:
            sys.argv.append("--reset")
        run_ingest()

    logger.info("\n==================================================================")
    logger.info("EDRAK Internal Knowledge ETL Pipeline Completed Successfully!")
    logger.info("==================================================================")


def main():
    parser = argparse.ArgumentParser(description="EDRAK Internal Knowledge ETL Pipeline.")
    parser.add_argument("--all", action="store_true", help="Run entire ETL pipeline (fetch, clean, chunk, ingest)")
    parser.add_argument("--fetch", action="store_true", help="Run scraper to fetch raw handbook pages")
    parser.add_argument("--clean", action="store_true", help="Run cleaner to process raw HTML into markdown")
    parser.add_argument("--chunk", action="store_true", help="Run chunker to split markdown into chunk JSONs")
    parser.add_argument("--ingest", action="store_true", help="Run ingestion into ChromaDB")
    parser.add_argument("--reset", action="store_true", help="Reset ChromaDB collection on ingestion")
    parser.add_argument("--url", type=str, default="https://handbook.gitlab.com/handbook/", help="Start URL")
    parser.add_argument("--max-pages", type=int, default=15, help="Max pages to crawl")

    args = parser.parse_args()

    run_all = args.all or (not args.fetch and not args.clean and not args.chunk and not args.ingest)
    do_fetch = args.fetch or run_all
    do_clean = args.clean or run_all
    do_chunk = args.chunk or run_all
    do_ingest = args.ingest or run_all

    run_pipeline(
        fetch=do_fetch,
        clean=do_clean,
        chunk=do_chunk,
        ingest=do_ingest,
        start_url=args.url,
        max_pages=args.max_pages,
        reset_db=args.reset,
    )


if __name__ == "__main__":
    main()


"""
python scripts/run_etl_pipeline.py --all --max-pages 25 --delay 0.2 --reset
"""
