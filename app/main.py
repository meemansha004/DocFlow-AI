from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import FRONTEND_URL
from app.routers import (
    access_requests,
    activity,
    admin,
    auth,
    documents,
    projects,
    workflow,
    workspace,
)

app = FastAPI(title="DocFlow AI", version="0.1.0")

# CORS for the local dev frontend (Vite on :5173). FRONTEND_URL comes from
# Phase 2 config; the extra localhost/127.0.0.1 variants cover both hostnames.
_cors_origins = sorted({
    FRONTEND_URL,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(workflow.router)
app.include_router(workspace.router)
app.include_router(projects.router)
app.include_router(admin.router)
app.include_router(access_requests.router)
app.include_router(activity.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
