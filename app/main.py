from fastapi import FastAPI

from app.routers import upload

app = FastAPI(title="DocFlow AI", version="0.1.0")

app.include_router(upload.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
