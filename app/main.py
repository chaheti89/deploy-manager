from fastapi import FastAPI, Request
from app.database import init_db
from app.embeddings import ingest_diff, retrieve_similar
import json

app = FastAPI(title="DeployGuard")

@app.on_event("startup")
async def startup():
    await init_db()

@app.get("/")
async def root():
    return {"message": "DeployGuard is running"}

@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    print("\n--- WEBHOOK RECEIVED ---")
    print(json.dumps(payload, indent=2))
    return {"status": "received"}

@app.post("/test-embed")
async def test_embed():
    n = await ingest_diff(
        commit_sha="abc123",
        repo="chaheti89/deploy-manager",
        diff="+ added payment gateway integration\n- removed old billing code\n+ new stripe webhook handler",
    )
    return {"chunks_stored": n}