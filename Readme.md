# DeployGuard

AI-powered deployment risk scoring. Every push to `master` is intercepted, its diff analysed by Claude, and a risk score is returned before the deploy proceeds.

## How it works

```
GitHub push → GitHub Actions → POST /webhook → fetch diff → pgvector similarity search → Claude scores → result stored in Postgres
```

## Stack

- **FastAPI** — webhook server
- **Claude (Anthropic)** — AI risk scoring
- **Voyage AI** — diff embeddings
- **pgvector (Postgres)** — similarity search over past deploys
- **Docker Compose** — Postgres + the app itself
- **slowapi** — rate limiting on /webhook

## Setup

### 1. Start everything with Docker Compose

```bash
cp .env.example .env
# Fill in your API keys in .env
docker compose up --build
```

This starts both Postgres (with pgvector) and the FastAPI app. Tables are created automatically on first start.

### For local development (without Docker for the app)

```bash
# Start just the DB
docker compose up db -d

pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 2. Expose locally for GitHub webhooks (dev)

```bash
ngrok http 8000
```

Copy the ngrok URL → GitHub repo Settings → Webhooks → Add webhook.  
Content type: `application/json`. Secret: your `GITHUB_WEBHOOK_SECRET`.

### 3. Configure GitHub Actions

Add these secrets in your repo Settings → Secrets:
- `DEPLOYGUARD_WEBHOOK_URL` — your server URL (e.g. ngrok URL or prod domain)
- `DEPLOYGUARD_WEBHOOK_SECRET` — same value as `GITHUB_WEBHOOK_SECRET` in `.env`

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres connection string |
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `GITHUB_WEBHOOK_SECRET` | ✅ | HMAC secret shared with GitHub |
| `VOYAGE_API_KEY` | ✅ | Voyage AI key for embeddings |
| `GITHUB_TOKEN` | optional | Raises GitHub API rate limit 60→5000/hr |
| `SLACK_WEBHOOK_URL` | optional | Slack incoming webhook URL for alerts |
| `SMTP_HOST` | optional | SMTP server for email alerts (e.g. smtp.gmail.com) |
| `SMTP_PORT` | optional | SMTP port (default: 587) |
| `SMTP_USER` | optional | Sender email address |
| `SMTP_PASSWORD` | optional | SMTP password or app password |
| `ALERT_EMAIL_TO` | optional | Recipient address for HIGH/CRITICAL alerts |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| POST | `/webhook` | Receives GitHub push events (rate limited: 30/min per IP) |
| GET | `/deploys` | List recent scored deploys (`?limit=N&repo=owner/name`) |
| GET | `/deploys/{id}` | Single deploy detail including full diff |
| GET | `/dashboard` | Visual deploy history dashboard |
| POST | `/test-score` | Test Claude scoring (no DB/embed) |
| POST | `/test-embed` | Test Voyage AI embedding |

## Risk Levels

| Score | Level | Action |
|-------|-------|--------|
| 0.0–0.3 | 🟢 LOW | Deploy proceeds |
| 0.4–0.6 | 🟡 MEDIUM | Deploy proceeds |
| 0.6–0.8 | 🟠 HIGH | Deploy proceeds + Slack/email alert sent |
| 0.8–1.0 | 🔴 CRITICAL | **Deploy blocked** (exit 1) + Slack/email alert sent |

## Notifications

**Slack** — Set `SLACK_WEBHOOK_URL` in `.env`. Alerts fire on HIGH and CRITICAL deploys.

**Email** — Set all five `SMTP_*` and `ALERT_EMAIL_TO` vars in `.env`. Alerts fire on HIGH and CRITICAL deploys with an HTML email.

Both run concurrently and are non-fatal — a notification failure never blocks the scoring response.
