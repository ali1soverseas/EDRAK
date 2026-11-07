"""CLI script to push saved Company Profile and Research Facts into ChromaDB RAG.

Usage:
    python scripts/push_company_facts_to_rag.py --workspace demo
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys

# Ensure backend/src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from edrak.core.config import settings
from edrak.rag.profile_bridge import index_company_profile, sync_workspace_facts_to_rag
from edrak.core.profiles import COMPANY_PROFILES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("push_company_facts_to_rag")


def main():
    parser = argparse.ArgumentParser(description="Push Company Profile & Research Facts into ChromaDB RAG.")
    parser.add_argument("--workspace", default=settings.DEMO_WORKSPACE_ID, help="Target workspace ID")
    args = parser.parse_args()
    workspace_id = args.workspace.strip()

    logger.info("==================================================================")
    logger.info("Starting Profile & Facts to RAG Sync for workspace: %s", workspace_id)
    logger.info("==================================================================")

    # 1. Index Profile Fixture / Model into RAG
    profile = COMPANY_PROFILES.get(workspace_id) or COMPANY_PROFILES.get("nvidia")
    if profile:
        profile_chunks = index_company_profile(workspace_id, profile)
        logger.info("Successfully indexed profile for '%s' (%d chunks) into RAG.", profile.name, profile_chunks)

    # 2. Sync facts from database if DB connection is available
    try:
        from edrak.db.connection import get_db

        async def _sync():
            async with get_db() as db:
                return await sync_workspace_facts_to_rag(workspace_id, db)

        fact_chunks = asyncio.run(_sync())
        logger.info("Successfully indexed %d fact chunks from database into RAG.", fact_chunks)
    except ModuleNotFoundError as e:
        logger.info("Database driver not installed in this environment (%s). Skipping SQLite table sync.", e)
    except Exception as e:
        logger.warning("Could not sync facts from database: %s", e)

    logger.info("Profile & Facts to RAG sync completed successfully!")


if __name__ == "__main__":
    main()
