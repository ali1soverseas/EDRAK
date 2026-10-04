"""GitLab Handbook Document Chunker.

Reads cleaned Markdown documents from data/handbook/cleaned/, splits them into
semantically coherent chunks with provenance metadata, and outputs structured chunk records
into data/handbook/chunked/.
"""

import argparse
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure backend/src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from edrak.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chunk_handbook")


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Extracts YAML frontmatter metadata and body text."""
    frontmatter = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_fm = parts[1].strip()
            body = parts[2].strip()
            for line in raw_fm.split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    frontmatter[key.strip()] = val.strip().strip("\"'")

    return frontmatter, body


class HandbookChunker:
    """Splits Markdown documents into structured semantic chunks with rich metadata."""

    def __init__(
        self,
        clean_dir: Optional[Path] = None,
        chunk_dir: Optional[Path] = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
    ):
        self.clean_dir = clean_dir or settings.get_absolute_cleaned_handbook_path()
        self.chunk_dir = chunk_dir or settings.get_absolute_chunked_handbook_path()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.chunk_dir.mkdir(parents=True, exist_ok=True)

    def chunk_markdown(
        self,
        text: str,
        source_uri: str,
        title: str,
        section: str,
        doc_type: str = "internal_handbook",
    ) -> List[Dict[str, Any]]:
        """Splits markdown by headings and paragraphs into semantic chunks."""
        chunks: List[Dict[str, Any]] = []

        # Split text by top-level or sub-level markdown headings
        heading_pattern = re.compile(r"^(#{1,4}\s+.+)$", re.MULTILINE)
        sections = heading_pattern.split(text)

        current_heading = title
        current_paras: List[str] = []
        current_len = 0

        i = 0
        while i < len(sections):
            item = sections[i].strip()
            i += 1
            if not item:
                continue

            # Check if this item is a heading
            if item.startswith("#"):
                current_heading = item.lstrip("#").strip()
                continue

            # Split paragraph blocks
            paragraphs = item.split("\n\n")
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                para_len = len(para)
                if current_len + para_len > self.chunk_size and current_paras:
                    # Flush current chunk
                    chunk_text = "\n\n".join(current_paras)
                    chunk_hash = hashlib.md5(f"{source_uri}:{current_heading}:{chunk_text[:60]}".encode()).hexdigest()[:12]
                    chunk_id = f"chunk_hb_{chunk_hash}"

                    chunks.append({
                        "chunk_id": chunk_id,
                        "source_uri": source_uri,
                        "title": title,
                        "section": section,
                        "heading": current_heading,
                        "doc_type": doc_type,
                        "content": f"## {current_heading}\n\n{chunk_text}" if current_heading and not chunk_text.startswith("#") else chunk_text,
                        "char_count": len(chunk_text),
                    })

                    current_paras = [para]
                    current_len = para_len
                else:
                    current_paras.append(para)
                    current_len += para_len + 2

        if current_paras:
            chunk_text = "\n\n".join(current_paras)
            chunk_hash = hashlib.md5(f"{source_uri}:{current_heading}:{chunk_text[:60]}".encode()).hexdigest()[:12]
            chunk_id = f"chunk_hb_{chunk_hash}"

            chunks.append({
                "chunk_id": chunk_id,
                "source_uri": source_uri,
                "title": title,
                "section": section,
                "heading": current_heading,
                "doc_type": doc_type,
                "content": f"## {current_heading}\n\n{chunk_text}" if current_heading and not chunk_text.startswith("#") else chunk_text,
                "char_count": len(chunk_text),
            })

        return chunks

    def process_all(self) -> int:
        """Processes all clean markdown files and outputs chunk JSON files."""
        logger.info("Starting chunking of clean documents in %s...", self.clean_dir)
        clean_files = list(self.clean_dir.glob("*.md"))
        if not clean_files:
            logger.warning("No clean markdown files found in %s to chunk.", self.clean_dir)
            return 0

        total_chunks = 0
        for clean_file in clean_files:
            try:
                raw_content = clean_file.read_text(encoding="utf-8")
                frontmatter, body = parse_frontmatter(raw_content)

                source_url = frontmatter.get("source_url", f"handbook://{clean_file.stem}")
                title = frontmatter.get("title", clean_file.stem.replace("_", " ").title())
                section = frontmatter.get("section", "handbook")
                doc_type = frontmatter.get("doc_type", "internal_handbook")

                doc_chunks = self.chunk_markdown(
                    text=body,
                    source_uri=source_url,
                    title=title,
                    section=section,
                    doc_type=doc_type,
                )

                if not doc_chunks:
                    continue

                out_filename = clean_file.stem + "_chunks.json"
                out_path = self.chunk_dir / out_filename
                out_path.write_text(json.dumps(doc_chunks, indent=2, ensure_ascii=False), encoding="utf-8")
                total_chunks += len(doc_chunks)
                logger.info("Generated %d chunks -> %s", len(doc_chunks), out_path.name)
            except Exception as e:
                logger.error("Failed to chunk %s: %s", clean_file.name, e)

        logger.info("Successfully produced %d chunks in %s", total_chunks, self.chunk_dir)
        return total_chunks


def main():
    parser = argparse.ArgumentParser(description="Chunk cleaned GitLab handbook Markdown files.")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Target chunk size in characters")
    parser.add_argument("--overlap", type=int, default=150, help="Overlap size in characters")
    args = parser.parse_args()

    chunker = HandbookChunker(chunk_size=args.chunk_size, chunk_overlap=args.overlap)
    chunker.process_all()


if __name__ == "__main__":
    main()
