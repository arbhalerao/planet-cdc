import asyncio

from fastapi import APIRouter

router = APIRouter(prefix="/worker", tags=["worker"])


def _celery_inspect():
    from worker.celery_app import celery_app

    inspector = celery_app.control.inspect(timeout=2.0)
    active = inspector.active() or {}
    reserved = inspector.reserved() or {}
    return active, reserved


@router.get("/status")
async def worker_status():
    try:
        active, reserved = await asyncio.get_event_loop().run_in_executor(None, _celery_inspect)
    except Exception as exc:
        return {
            "error": str(exc),
            "workers": [],
            "active_tasks": [],
            "queued_tasks": [],
            "total_active": 0,
            "total_queued": 0,
        }

    workers = sorted(set(list(active.keys()) + list(reserved.keys())))

    active_tasks = [
        {
            "id": t.get("id"),
            "name": t.get("name", "").split(".")[-1],
            "full_name": t.get("name"),
            "args": t.get("args", []),
            "worker": worker,
            "time_start": t.get("time_start"),
        }
        for worker, tasks in active.items()
        for t in tasks
    ]

    queued_tasks = [
        {
            "id": t.get("id"),
            "name": t.get("name", "").split(".")[-1],
            "full_name": t.get("name"),
            "args": t.get("args", []),
            "worker": worker,
        }
        for worker, tasks in reserved.items()
        for t in tasks
    ]

    return {
        "workers": workers,
        "active_tasks": active_tasks,
        "queued_tasks": queued_tasks,
        "total_active": len(active_tasks),
        "total_queued": len(queued_tasks),
    }
