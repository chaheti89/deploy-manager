import asyncio
import httpx
from sqlalchemy import text
from app.database import AsyncSessionLocal, DiffEmbeddingDB
from app.config import settings


def chunk_diff(diff: str, chunk_size: int = 500) -> list[str]:
    """Split a large diff into smaller chunks for embedding."""
    lines = diff.split("\n")
    chunks = []
    current_chunk = []
    current_size = 0

    for line in lines:
        current_chunk.append(line)
        current_size += len(line)
        if current_size >= chunk_size:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_size = 0

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks if chunks else [diff]


async def embed_text(text_input: str, max_retries: int = 3) -> list[float]:
    """
    Async call to Voyage AI to embed a single text chunk.
    Retries with exponential backoff on rate limits (429) or server errors (5xx).
    """
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.voyageai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {settings.voyage_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": "voyage-3", "input": text_input},
                )

            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt * 3  # 3s, 6s, 12s
                print(f"[embeddings] Voyage AI returned {resp.status_code} (attempt {attempt + 1}/{max_retries}), waiting {wait}s...")
                if attempt == max_retries - 1:
                    resp.raise_for_status()
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

        except httpx.TimeoutException:
            wait = 2 ** attempt * 2
            print(f"[embeddings] Voyage AI timeout (attempt {attempt + 1}/{max_retries}), waiting {wait}s...")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(wait)

    raise RuntimeError("Voyage AI embedding failed after all retries")


async def ingest_diff(commit_sha: str, repo: str, diff: str) -> int:
    """
    Chunk a diff, embed each chunk concurrently, and store in pgvector.
    All HTTP calls are non-blocking — the event loop stays free.
    """
    chunks = chunk_diff(diff)

    # Embed all chunks concurrently rather than one at a time
    embeddings = await asyncio.gather(*[embed_text(chunk) for chunk in chunks])

    async with AsyncSessionLocal() as session:
        for chunk, embedding in zip(chunks, embeddings):
            record = DiffEmbeddingDB(
                commit_sha=commit_sha,
                repo=repo,
                chunk_text=chunk,
                embedding=embedding,
            )
            session.add(record)
        await session.commit()

    return len(chunks)


async def retrieve_similar(diff: str, repo: str, top_k: int = 5) -> list[str]:
    """Find the most similar past diff chunks using pgvector cosine similarity."""
    query_embedding = await embed_text(diff[:500])
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT chunk_text
                FROM diff_embeddings
                WHERE repo = :repo
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :top_k
            """),
            {
                "repo": repo,
                "embedding": str(query_embedding),
                "top_k": top_k,
            },
        )
        rows = result.fetchall()
    return [row[0] for row in rows]
