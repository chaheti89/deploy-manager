import hashlib
import hmac
import json
import httpx
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, desc

from app.config import settings
from app.utils import _utcnow
from app.database import init_db, AsyncSessionLocal, DeployEventDB
from app.embeddings import ingest_diff
from app.scorer import score_deploy
from app.notifications import notify_slack, notify_email

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="DeployGuard", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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
    similar_past_deploys: list[str],
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
            similar_past_deploys=json.dumps(similar_past_deploys),
            timestamp=_utcnow(),
        )
        session.add(event)
        await session.commit()


async def _ingest_diff_safe(commit_sha: str, repo: str, diff: str) -> None:
    try:
        n = await ingest_diff(commit_sha=commit_sha, repo=repo, diff=diff)
        print(f"[embeddings] stored {n} chunks for {repo}@{commit_sha[:7]}")
    except Exception as e:
        print(f"[embeddings] ingest failed for {repo}@{commit_sha[:7]} (non-fatal): {e}")


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

    # 2. Extract fields from GitHub push event
    repo = payload.get("repository", {}).get("full_name", "unknown/unknown")
    branch = payload.get("ref", "").replace("refs/heads/", "")
    head_commit = payload.get("head_commit", {})
    commit_sha = head_commit.get("id", payload.get("commit", "unknown"))
    author = head_commit.get("author", {}).get("name", "unknown")
    before_sha = payload.get("before", "")

    print(f"[webhook] push received: {repo}@{commit_sha[:7]} by {author} on {branch}")

    # 3. Fetch the actual diff from GitHub
    diff = ""
    is_initial_push = not before_sha or set(before_sha) == {"0"}
    if not is_initial_push:
        diff = await _fetch_github_diff(repo, before_sha, commit_sha)
    else:
        diff = f"[Initial push — no base to diff against]\nCommit: {commit_sha}"

    if not diff:
        diff = "[Empty diff — no changes detected]"

    # 4. Ingest diff into pgvector (background — non-blocking)
    asyncio.create_task(_ingest_diff_safe(commit_sha=commit_sha, repo=repo, diff=diff))

    # 5. Score the deploy with Claude (60 s hard ceiling so GH Actions never hangs)
    try:
        risk = await asyncio.wait_for(
            score_deploy(
                repo=repo,
                commit_sha=commit_sha,
                author=author,
                branch=branch,
                diff=diff,
            ),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        print(f"[scorer] scoring timed out for {repo}@{commit_sha[:7]}")
        return {
            "status": "timeout",
            "scored": False,
            "error": "Scoring timed out after 60 s — deploy not blocked",
            "commit": commit_sha,
        }
    except Exception as e:
        print(f"[scorer] scoring failed: {e}")
        return {
            "status": "received",
            "scored": False,
            "error": str(e),
            "commit": commit_sha,
        }

    # 6. Persist to DB and send notifications concurrently (all non-fatal)

    await asyncio.gather(
        _save_deploy_event(repo, commit_sha, author, branch, diff, risk.similar_past_deploys, risk),
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
async def list_deploys(limit: int = 20, offset: int = 0, repo: Optional[str] = None):
    """Return recent scored deploy events. Supports ?limit=N&offset=N&repo=owner/name."""
    async with AsyncSessionLocal() as session:
        q = select(DeployEventDB)
        if repo:
            q = q.where(DeployEventDB.repo == repo)
        q = q.order_by(desc(DeployEventDB.timestamp)).limit(limit).offset(offset)
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
            "similar_past_deploys": json.loads(r.similar_past_deploys) if r.similar_past_deploys else [],
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
        "similar_past_deploys": json.loads(r.similar_past_deploys) if r.similar_past_deploys else [],
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
    }


# ── Dashboard ──────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the deploy history dashboard."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── Dev / test endpoints ───────────────────────────────────────────

@app.post("/test-embed")
async def test_embed(repo: str = "test/repo"):
    n = await ingest_diff(
        commit_sha="test_embed",
        repo=repo,
        diff="+ added payment gateway integration\n- removed old billing code\n+ new stripe webhook handler",
    )
    return {"chunks_stored": n, "repo": repo}


@app.post("/test-score")
async def test_score(repo: str = "test/repo", author: str = "dev"):
    result = await score_deploy(
        repo=repo,
        commit_sha="test_score",
        author=author,
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