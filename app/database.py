from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import Column, String, Float, DateTime, Text, Integer, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from pgvector.sqlalchemy import Vector
from app.config import settings
from app.utils import _utcnow

engine = create_async_engine(settings.database_url, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class DeployEventDB(Base):
    __tablename__ = "deploy_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo = Column(String, nullable=False)
    commit_sha = Column(String, nullable=False)
    author = Column(String, nullable=False)
    branch = Column(String, nullable=False)
    diff = Column(Text, nullable=False)
    risk_score = Column(Float, nullable=True)
    risk_level = Column(String, nullable=True)
    blast_radius = Column(Text, nullable=True)
    reasoning = Column(Text, nullable=True)
    fix_recommendations = Column(Text, nullable=True)    # JSON-encoded list
    similar_past_deploys = Column(Text, nullable=True)   # JSON-encoded list
    timestamp = Column(DateTime, default=_utcnow)

class DiffEmbeddingDB(Base):
    __tablename__ = "diff_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commit_sha = Column(String, nullable=False)
    repo = Column(String, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(1024))      # Voyage AI (voyage-3) embedding dimension
    timestamp = Column(DateTime, default=_utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Safe migration: add column if an older DB already exists without it
        await conn.execute(text(
            "ALTER TABLE deploy_events ADD COLUMN IF NOT EXISTS similar_past_deploys TEXT"
        ))
        # Indexes for the two hot query paths
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_deploy_events_timestamp "
            "ON deploy_events (timestamp DESC)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_deploy_events_repo "
            "ON deploy_events (repo)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_diff_embeddings_repo "
            "ON diff_embeddings (repo)"
        ))