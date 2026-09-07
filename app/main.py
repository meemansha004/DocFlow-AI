from fastapi import FastAPI

from app.routers import auth, documents, workflow

app = FastAPI(title="DocFlow AI", version="0.1.0")

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(workflow.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
