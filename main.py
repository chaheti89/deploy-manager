from fastapi import FastAPI, Request
import json
app = FastAPI()
@app.get("/")
async def root():
    return {"message":"Hello World"}
@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    print("\n--- WEBHOOK RECEIVED ---")
    print(json.dumps(payload, indent=2))
    print("------------------------\n")
    return {"status": "received"}