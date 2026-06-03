import anthropic
import asyncio
import json

from app.config import settings
from app.utils import _utcnow
from app.models import RiskScore, RiskLevel
from app.embeddings import retrieve_similar

# Use the async client so we don't block the event loop
client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _determine_risk_level(score: float) -> RiskLevel:
    if score >= 0.8:
        return RiskLevel.CRITICAL
    elif score >= 0.6:
        return RiskLevel.HIGH
    elif score >= 0.4:
        return RiskLevel.MEDIUM
    else:
        return RiskLevel.LOW


async def _call_claude_with_retry(prompt: str, max_retries: int = 3) -> str:
    """
    Call Claude with exponential backoff for rate limits.
    Free-tier keys hit rate limits quickly — this makes the system resilient.
    """
    for attempt in range(max_retries):
        try:
            message = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except anthropic.RateLimitError as e:
            wait = 2 ** attempt * 5  # 5s, 10s, 20s
            print(f"[scorer] Rate limit hit (attempt {attempt + 1}/{max_retries}), waiting {wait}s...")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code == 529:  # overloaded
                wait = 2 ** attempt * 3
                print(f"[scorer] API overloaded, waiting {wait}s...")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(wait)
            else:
                raise


async def score_deploy(repo: str, commit_sha: str, author: str, branch: str, diff: str) -> RiskScore:
    # Step 1: Retrieve similar past diffs from pgvector
    similar_chunks = await retrieve_similar(diff, repo, top_k=5)
    similar_context = "\n---\n".join(similar_chunks) if similar_chunks else "No past deploys found."

    # Step 2: Build prompt for Claude
    prompt = f"""You are DeployGuard, an expert system that analyzes code diffs before deployment and returns a structured risk assessment.

## Current Deploy Info
- Repo: {repo}
- Commit: {commit_sha}
- Author: {author}
- Branch: {branch}

## Code Diff

{diff[:3000]}

## Similar Past Deploys (retrieved context)
{similar_context}

## Your Task
Analyze this diff and return a JSON object with EXACTLY these fields:
{{
  "score": <float between 0.0 and 1.0>,
  "blast_radius": "<which systems, services, or users are affected>",
  "fix_recommendations": ["<recommendation 1>", "<recommendation 2>", "<recommendation 3>"],
  "reasoning": "<2-3 sentence explanation of the risk assessment>"
}}

Scoring guide:
- 0.0–0.3: Low risk (docs, tests, minor fixes)
- 0.4–<0.6: Medium risk (new features, refactors)
- 0.6–<0.8: High risk (auth, payments, DB migrations)
- 0.8–1.0: Critical risk (security vulnerabilities, data deletion, prod config changes)

Return ONLY the JSON object, no other text."""

    # Step 3: Call Claude (with retry for free-tier rate limits)
    raw = await _call_claude_with_retry(prompt)

    # Step 4: Parse — strip markdown fences if Claude added them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude returned non-JSON response (first 200 chars): {raw[:200]}"
        ) from exc

    return RiskScore(
        score=float(data["score"]),
        risk_level=_determine_risk_level(float(data["score"])),
        blast_radius=data["blast_radius"],
        fix_recommendations=data["fix_recommendations"],
        similar_past_deploys=similar_chunks,
        reasoning=data["reasoning"],
        evaluated_at=_utcnow(),
    )