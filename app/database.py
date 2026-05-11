from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import Column, String, Float, DateTime, Text, Integer, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from pgvector.sqlalchemy import Vector
from datetime import datetime
from app.config import settings

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
    timestamp = Column(DateTime, default=datetime.utcnow)

class DiffEmbeddingDB(Base):
    __tablename__ = "diff_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commit_sha = Column(String, nullable=False)
    repo = Column(String, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(1024))      # OpenAI/Claude embedding dimension
    timestamp = Column(DateTime, default=datetime.utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)