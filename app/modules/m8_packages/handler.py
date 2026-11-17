"""M8 路由：工作包清单、派单同步、包明细与发布阻断。"""
from __future__ import annotations

from fastapi import APIRouter

from ..m1_intake import service as intake_service
from . import service

router = APIRouter(prefix="/api/packages", tags=["m8-packages"])


@router.get("")
def list_packages():
    run = intake_service.ensure_run()
    run_id = run["run_id"]
    return {"run_id": run_id, "stats": service.stats(run_id),
            "items": service.packages_view(run_id)}


@router.post("/sync")
def sync_packages():
    run = intake_service.ensure_run()
    return service.ensure_packages(run["run_id"])


@router.get("/blockers")
def package_blockers():
    run = intake_service.ensure_run()
    run_id = run["run_id"]
    items = service.publish_blockers(run_id)
    return {"run_id": run_id, "total": len(items), "items": items}


@router.get("/{package_id}")
def package_detail(package_id: str):
    run = intake_service.ensure_run()
    return service.package_detail(run["run_id"], package_id)
