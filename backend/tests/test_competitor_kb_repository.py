import aiosqlite
import pytest

from edrak.db.repositories.competitor_kb import list_pages, prune_pages


@pytest.mark.asyncio
async def test_list_pages_reconstructs_chunks_in_reading_order():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    try:
        await db.execute(
            """
            CREATE TABLE competitor_kb_chunks (
                kb_key TEXT, source_url TEXT, title TEXT, source_type TEXT, page_type TEXT,
                market TEXT, language TEXT, chunk_index INTEGER, content TEXT, fetched_at TEXT
            )
            """
        )
        await db.executemany(
            "INSERT INTO competitor_kb_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("example.com", "https://example.com/about", "About", "official", "about", "any", "en",
                 1, "Second paragraph.", "2026-01-02"),
                ("example.com", "https://example.com/about", "About", "official", "about", "any", "en",
                 0, "First paragraph.", "2026-01-01"),
                ("example.com", "https://another.com/", "Home", "third_party", "home", "any", "en",
                 0, "Another page.", "2026-01-03"),
                ("different-kb.com", "https://different-kb.com/", "Other KB", "official", "home", "any", "en",
                 0, "Must not be included.", "2026-01-03"),
            ],
        )
        await db.commit()

        pages = await list_pages(db, "example.com")
    finally:
        await db.close()

    assert pages == [
        {
            "url": "https://another.com/",
            "title": "Home",
            "source_type": "third_party",
            "page_type": "home",
            "market": "any",
            "language": "en",
            "fetched_at": "2026-01-03",
            "text": "Another page.",
        },
        {
            "url": "https://example.com/about",
            "title": "About",
            "source_type": "official",
            "page_type": "about",
            "market": "any",
            "language": "en",
            "fetched_at": "2026-01-02",
            "text": "First paragraph.\n\nSecond paragraph.",
        },
    ]


@pytest.mark.asyncio
async def test_prune_pages_requires_market_and_preserves_other_market_pages():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    try:
        await db.execute(
            "CREATE TABLE competitor_kb_chunks (kb_key TEXT, source_url TEXT, market TEXT)"
        )
        await db.executemany(
            "INSERT INTO competitor_kb_chunks VALUES (?, ?, ?)",
            [
                ("example.com", "https://example.com/old-us", "United States"),
                ("example.com", "https://example.com/old-eu", "Europe"),
                ("example.com", "https://example.com/any", "any"),
            ],
        )

        removed = await prune_pages(db, "example.com", [], "United States")
        remaining = await db.execute_fetchall(
            "SELECT source_url, market FROM competitor_kb_chunks ORDER BY source_url"
        )
    finally:
        await db.close()

    assert removed == ["https://example.com/old-us", "https://example.com/any"]
    assert [tuple(row) for row in remaining] == [
        ("https://example.com/old-eu", "Europe"),
    ]
