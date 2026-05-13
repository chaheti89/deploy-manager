import hashlib
import hmac
import json
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, desc


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC for DB compat

from app.config import settings
from app.database import init_db, AsyncSessionLocal, DeployEventDB
from app.embeddings import ingest_diff
from app.scorer import score_deploy
from app.notifications import notify_slack, notify_email

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="DeployGuard")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ── Startup ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()


# ── Health ─────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "DeployGuard is running"}


# ── Helpers ────────────────────────────────────────────────────────

def _verify_github_signature(body: bytes, sig_header: Optional[str]) -> bool:
    """HMAC-SHA256 verification for GitHub webhook payloads."""
    if not settings.github_webhook_secret:
        return True  # secret not configured — skip in dev
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)


async def _fetch_github_diff(repo: str, base_sha: str, head_sha: str) -> str:
    """
    Fetch the unified diff between two commits via the GitHub compare API.
    Returns the combined patch text, truncated to 8 000 chars if very large.
    """
    url = f"https://api.github.com/repos/{repo}/compare/{base_sha}...{head_sha}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    github_token = getattr(settings, "github_token", None)
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code != 200:
        return f"[Could not fetch diff: GitHub API returned {resp.status_code}]"

    data = resp.json()
    patches = []
    for f in data.get("files", []):
        patch = f.get("patch", "")
        if patch:
            patches.append(f"### {f['filename']}\n{patch}")

    full_diff = "\n\n".join(patches)
    return full_diff[:8000] if len(full_diff) > 8000 else full_diff


async def _save_deploy_event(
    repo: str,
    commit_sha: str,
    author: str,
    branch: str,
    diff: str,
    risk_score,
) -> None:
    """Persist the scored deploy event to the database."""
    async with AsyncSessionLocal() as session:
        event = DeployEventDB(
            repo=repo,
            commit_sha=commit_sha,
            author=author,
            branch=branch,
            diff=diff[:4000],
            risk_score=risk_score.score,
            risk_level=risk_score.risk_level.value,
            blast_radius=risk_score.blast_radius,
            reasoning=risk_score.reasoning,
            fix_recommendations=json.dumps(risk_score.fix_recommendations),
            timestamp=_utcnow(),
        )
        session.add(event)
        await session.commit()


# ── Main webhook ───────────────────────────────────────────────────

@app.post("/webhook")
@limiter.limit("30/minute")
async def webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
):
    body = await request.body()

    # 1. Verify GitHub signature
    if not _verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    print("\n--- WEBHOOK RECEIVED ---")
    print(json.dumps(payload, indent=2)[:500])
    print("------------------------\n")

    # 2. Extract fields from GitHub push event
    repo = payload.get("repository", {}).get("full_name", "unknown/unknown")
    branch = payload.get("ref", "").replace("refs/heads/", "")
    head_commit = payload.get("head_commit", {})
    commit_sha = head_commit.get("id", payload.get("commit", "unknown"))
    author = head_commit.get("author", {}).get("name", "unknown")
    before_sha = payload.get("before", "")

    # 3. Fetch the actual diff from GitHub
    diff = ""
    is_initial_push = not before_sha or set(before_sha) == {"0"}
    if not is_initial_push:
        diff = await _fetch_github_diff(repo, before_sha, commit_sha)
    else:
        diff = f"[Initial push — no base to diff against]\nCommit: {commit_sha}"

    if not diff:
        diff = "[Empty diff — no changes detected]"

    # 4. Ingest diff into pgvector (non-fatal)
    try:
        await ingest_diff(commit_sha=commit_sha, repo=repo, diff=diff)
    except Exception as e:
        print(f"[embeddings] ingest failed (non-fatal): {e}")

    # 5. Score the deploy with Claude
    try:
        risk = await score_deploy(
            repo=repo,
            commit_sha=commit_sha,
            author=author,
            branch=branch,
            diff=diff,
        )
    except Exception as e:
        print(f"[scorer] scoring failed: {e}")
        return {
            "status": "received",
            "scored": False,
            "error": str(e),
            "commit": commit_sha,
        }

    # 6. Persist to DB and send notifications concurrently (all non-fatal)
    import asyncio as _asyncio
    await _asyncio.gather(
        _save_deploy_event(repo, commit_sha, author, branch, diff, risk),
        notify_slack(repo, commit_sha, author, branch, risk),
        notify_email(repo, commit_sha, author, branch, risk),
        return_exceptions=True,
    )

    print(f"[DeployGuard] {repo}@{commit_sha[:7]} → {risk.risk_level.value.upper()} ({risk.score:.2f})")

    return {
        "status": "scored",
        "commit": commit_sha[:7],
        "author": author,
        "branch": branch,
        "risk_level": risk.risk_level.value,
        "risk_score": round(risk.score, 3),
        "blast_radius": risk.blast_radius,
        "fix_recommendations": risk.fix_recommendations,
        "reasoning": risk.reasoning,
        "similar_past_deploys_count": len(risk.similar_past_deploys),
    }


# ── History endpoint ───────────────────────────────────────────────

@app.get("/deploys")
async def list_deploys(limit: int = 20, repo: Optional[str] = None):
    """Return recent scored deploy events."""
    async with AsyncSessionLocal() as session:
        q = select(DeployEventDB)
        if repo:
            q = q.where(DeployEventDB.repo == repo)
        q = q.order_by(desc(DeployEventDB.timestamp)).limit(limit)
        result = await session.execute(q)
        rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "repo": r.repo,
            "commit_sha": r.commit_sha[:7] if r.commit_sha else None,
            "author": r.author,
            "branch": r.branch,
            "risk_score": r.risk_score,
            "risk_level": r.risk_level,
            "blast_radius": r.blast_radius,
            "reasoning": r.reasoning,
            "fix_recommendations": json.loads(r.fix_recommendations) if r.fix_recommendations else [],
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in rows
    ]


@app.get("/deploys/{deploy_id}")
async def get_deploy(deploy_id: int):
    """Return a single deploy event by ID, including full diff."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DeployEventDB).where(DeployEventDB.id == deploy_id)
        )
        r = result.scalar_one_or_none()

    if r is None:
        raise HTTPException(status_code=404, detail="Deploy not found")

    return {
        "id": r.id,
        "repo": r.repo,
        "commit_sha": r.commit_sha,
        "author": r.author,
        "branch": r.branch,
        "diff": r.diff,
        "risk_score": r.risk_score,
        "risk_level": r.risk_level,
        "blast_radius": r.blast_radius,
        "reasoning": r.reasoning,
        "fix_recommendations": json.loads(r.fix_recommendations) if r.fix_recommendations else [],
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
    }


# ── Dashboard ──────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the deploy history dashboard."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── Dev / test endpoints ───────────────────────────────────────────

@app.post("/test-embed")
async def test_embed():
    n = await ingest_diff(
        commit_sha="abc123",
        repo="chaheti89/deploy-manager",
        diff="+ added payment gateway integration\n- removed old billing code\n+ new stripe webhook handler",
    )
    return {"chunks_stored": n}


@app.post("/test-score")
async def test_score():
    result = await score_deploy(
        repo="chaheti89/deploy-manager",
        commit_sha="def456",
        author="chaheti89",
        branch="master",
        diff="""
- def process_payment(card_number, amount):
-     charge(card_number, amount)
+ def process_payment(card_number, amount, user_id):
+     if not validate_card(card_number):
+         raise ValueError("Invalid card")
+     charge(card_number, amount)
+     log_transaction(user_id, amount)
        """,
    )
    return result