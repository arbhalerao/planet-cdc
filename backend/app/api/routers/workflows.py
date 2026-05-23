import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping, shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from worker.compat import check_compatibility
from worker.providers.registry import get_collection as get_provider_collection
from app.db.models.aoi import Aoi
from app.db.models.enums import WorkflowStatus
from app.db.models.results import WorkflowItem
from app.db.models.thresholds import ThresholdConfig
from app.db.models.workflow import (
    Workflow,
    WorkflowCollection,
    WorkflowModelCollectionConfig,
    WorkflowModelConfig,
)
from app.schemas.workflow import (
    CollectionConfigResponse,
    ModelConfigResponse,
    ThresholdConfigResponse,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowSummary,
    WorkflowUpdate,
)
from worker.models.registry import get_model
from worker.providers.registry import get_collection

router = APIRouter(prefix="/workflows", tags=["workflows"])


# helpers


def _parse_geometry(geojson: dict[str, Any]):
    geom_type = geojson.get("type", "")
    if geom_type not in ("Polygon", "MultiPolygon"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="geometry must be a GeoJSON Polygon or MultiPolygon",
        )
    try:
        shapely_geom = shape(geojson)
        if not shapely_geom.is_valid:
            raise ValueError("invalid geometry")
        return from_shape(shapely_geom, srid=4326)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid geometry: {exc}",
        )


async def _load_response(workflow: Workflow, db: AsyncSession) -> WorkflowResponse:
    aoi = (await db.execute(select(Aoi).where(Aoi.id == workflow.aoi_id))).scalar_one()
    aoi_geometry = mapping(to_shape(aoi.geometry))

    wc_rows = (
        (
            await db.execute(
                select(WorkflowCollection).where(WorkflowCollection.workflow_id == workflow.id)
            )
        )
        .scalars()
        .all()
    )

    wmc_rows = (
        (
            await db.execute(
                select(WorkflowModelConfig).where(WorkflowModelConfig.workflow_id == workflow.id)
            )
        )
        .scalars()
        .all()
    )

    model_configs = []
    for wmc in wmc_rows:
        cc_rows = (
            (
                await db.execute(
                    select(WorkflowModelCollectionConfig).where(
                        WorkflowModelCollectionConfig.workflow_model_config_id == wmc.id
                    )
                )
            )
            .scalars()
            .all()
        )

        tc_rows = (
            (
                await db.execute(
                    select(ThresholdConfig).where(
                        ThresholdConfig.workflow_model_config_id == wmc.id
                    )
                )
            )
            .scalars()
            .all()
        )

        model_configs.append(
            ModelConfigResponse(
                id=wmc.id,
                model_slug=wmc.model_slug,
                user_label=wmc.user_label,
                parameters=wmc.parameters,
                collection_configs=[
                    CollectionConfigResponse(
                        collection_slug=cc.collection_slug,
                        compatibility_level=cc.compatibility_level,
                        is_enabled=cc.is_enabled,
                    )
                    for cc in cc_rows
                ],
                threshold_configs=[
                    ThresholdConfigResponse(
                        score_name=tc.score_name,
                        green_min=tc.green_min,
                        green_max=tc.green_max,
                        yellow_min=tc.yellow_min,
                        yellow_max=tc.yellow_max,
                        red_min=tc.red_min,
                        red_max=tc.red_max,
                    )
                    for tc in tc_rows
                ],
            )
        )

    total_items = (
        await db.execute(select(func.count()).where(WorkflowItem.workflow_id == workflow.id))
    ).scalar_one()

    processed_items = (
        await db.execute(
            select(func.count()).where(
                WorkflowItem.workflow_id == workflow.id,
                WorkflowItem.status == "processed",
            )
        )
    ).scalar_one()

    identified_items = (
        await db.execute(
            select(func.count()).where(
                WorkflowItem.workflow_id == workflow.id,
                WorkflowItem.overall_severity.in_(["yellow", "red"]),
            )
        )
    ).scalar_one()

    next_run_at = None
    if workflow.poll_interval_minutes and workflow.last_checked_at:
        next_run_at = workflow.last_checked_at + timedelta(minutes=workflow.poll_interval_minutes)

    return WorkflowResponse(
        id=workflow.id,
        aoi_id=workflow.aoi_id,
        aoi_geometry=aoi_geometry,
        name=workflow.name,
        description=workflow.description,
        time_mode=workflow.time_mode,
        time_start=workflow.time_start,
        time_end=workflow.time_end,
        aoi_filter_mode=workflow.aoi_filter_mode,
        poll_interval_minutes=workflow.poll_interval_minutes,
        last_checked_at=workflow.last_checked_at,
        next_run_at=next_run_at,
        status=workflow.status,
        started_at=workflow.started_at,
        completed_at=workflow.completed_at,
        error_message=workflow.error_message,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        collection_slugs=[wc.collection_slug for wc in wc_rows],
        model_configs=model_configs,
        total_items=total_items,
        processed_items=processed_items,
        identified_items=identified_items,
    )


async def _get_workflow(workflow_id: uuid.UUID, db: AsyncSession) -> Workflow:
    workflow = (
        await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    ).scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return workflow


# routes


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
):
    if body.time_end <= body.time_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="time_end must be after time_start",
        )

    aoi = Aoi(
        name=body.name,
        geometry=_parse_geometry(body.geometry),
    )
    db.add(aoi)
    await db.flush()

    collection_infos = {}
    for slug in body.collection_slugs:
        try:
            _, info = get_collection(slug)
            collection_infos[slug] = info
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown collection '{slug}'",
            )

    model_objs = {}
    for mc in body.models:
        try:
            m = get_model(mc.model_slug)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown model '{mc.model_slug}'",
            )
        model_objs[mc.model_slug] = m

    workflow = Workflow(
        aoi_id=aoi.id,
        aoi_filter_mode=body.aoi_filter_mode,
        poll_interval_minutes=body.poll_interval_minutes,
        name=body.name,
        description=body.description,
        time_mode=body.time_mode,
        time_start=body.time_start,
        time_end=body.time_end,
        status=WorkflowStatus.draft,
    )
    db.add(workflow)
    await db.flush()

    for slug in body.collection_slugs:
        db.add(WorkflowCollection(workflow_id=workflow.id, collection_slug=slug))

    for mc_input in body.models:
        model = model_objs[mc_input.model_slug]
        wmc = WorkflowModelConfig(
            workflow_id=workflow.id,
            model_slug=mc_input.model_slug,
            user_label=mc_input.user_label,
            parameters=mc_input.parameters,
        )
        db.add(wmc)
        await db.flush()

        for col_slug in body.collection_slugs:
            try:
                _, col_info = get_provider_collection(col_slug)
                level = check_compatibility(model, col_info).level
            except KeyError:
                level = "incompatible"
            db.add(
                WorkflowModelCollectionConfig(
                    workflow_id=workflow.id,
                    workflow_model_config_id=wmc.id,
                    collection_slug=col_slug,
                    compatibility_level=level,
                    is_enabled=level != "incompatible",
                )
            )

        user_thresholds = mc_input.thresholds or {}
        for score_name, defaults in model.default_thresholds.items():
            ov = user_thresholds.get(score_name)
            db.add(
                ThresholdConfig(
                    workflow_model_config_id=wmc.id,
                    score_name=score_name,
                    green_min=ov.green_min if ov else defaults.green[0],
                    green_max=ov.green_max if ov else defaults.green[1],
                    yellow_min=ov.yellow_min if ov else defaults.yellow[0],
                    yellow_max=ov.yellow_max if ov else defaults.yellow[1],
                    red_min=ov.red_min if ov else defaults.red[0],
                    red_max=ov.red_max if ov else defaults.red[1],
                )
            )

    await db.commit()
    await db.refresh(workflow)
    return await _load_response(workflow, db)


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Workflow).order_by(Workflow.created_at.desc()))).scalars().all()
    return [
        WorkflowSummary(
            id=w.id,
            name=w.name,
            description=w.description,
            time_mode=w.time_mode,
            time_start=w.time_start,
            time_end=w.time_end,
            status=w.status,
            created_at=w.created_at,
            updated_at=w.updated_at,
        )
        for w in rows
    ]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    workflow = await _get_workflow(workflow_id, db)
    return await _load_response(workflow, db)


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
):
    workflow = await _get_workflow(workflow_id, db)
    if workflow.status != WorkflowStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft workflows can be updated",
        )
    if body.name is not None:
        workflow.name = body.name
    if body.description is not None:
        workflow.description = body.description
    workflow.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(workflow)
    return await _load_response(workflow, db)


@router.post("/{workflow_id}/run", response_model=WorkflowResponse)
async def run_workflow(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    workflow = await _get_workflow(workflow_id, db)
    if workflow.status not in (WorkflowStatus.draft, WorkflowStatus.failed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot run a workflow with status '{workflow.status.value}'",
        )

    workflow.status = WorkflowStatus.running
    workflow.started_at = datetime.now(timezone.utc)
    workflow.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(workflow)

    from worker.tasks import run_workflow as celery_run_workflow

    celery_run_workflow.delay(str(workflow_id))

    return await _load_response(workflow, db)


@router.post("/{workflow_id}/fetch-now", response_model=WorkflowResponse)
async def fetch_now(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    workflow = await _get_workflow(workflow_id, db)
    if workflow.time_mode != "fixed_future" or not workflow.poll_interval_minutes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="fetch-now is only available for fixed_future workflows with a monitor interval",
        )
    if workflow.status == WorkflowStatus.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow is already running",
        )
    workflow.status = WorkflowStatus.running
    workflow.started_at = datetime.now(timezone.utc)
    workflow.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(workflow)

    from worker.tasks import run_workflow as celery_run_workflow

    celery_run_workflow.delay(str(workflow_id))

    return await _load_response(workflow, db)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    workflow = await _get_workflow(workflow_id, db)
    if workflow.status == WorkflowStatus.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a running workflow",
        )
    await db.delete(workflow)
    await db.commit()
