from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class DeployEvent(BaseModel):
    repo: str
    commit_sha: str
    author: str
    branch: str
    diff: str
    timestamp: datetime

class RiskScore(BaseModel):
    score: float                        # 0.0 to 1.0
    risk_level: RiskLevel
    blast_radius: str                   # what systems/files are affected
    fix_recommendations: list[str]      # actionable steps
    similar_past_deploys: list[str]     # retrieved from pgvector
    reasoning: str                      # Claude's explanation
    evaluated_at: datetime