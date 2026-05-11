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


def embed_text(text_input: str) -> list[float]:
    """Call Voyage AI to embed a single text chunk."""
    resp = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {settings.voyage_api_key}",
            "Content-Type": "application/json",
        },
        json={"model": "voyage-3", "input": text_input},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def ingest_diff(commit_sha: str, repo: str, diff: str):
    """Chunk a diff, embed each chunk, and store in pgvector."""
    chunks = chunk_diff(diff)
    async with AsyncSessionLocal() as session:
        for chunk in chunks:
            embedding = embed_text(chunk)
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
    query_embedding = embed_text(diff[:500])
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