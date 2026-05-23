from fastapi import FastAPI

from app.api.routers import catalogue, results, workflows
from app.api.routers import worker as worker_router

app = FastAPI(title="Planet CDC")

app.include_router(catalogue.router)
app.include_router(workflows.router)
app.include_router(results.router)
app.include_router(worker_router.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
