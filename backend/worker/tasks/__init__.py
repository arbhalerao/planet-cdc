import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from celery import chord, group
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping, shape
from sqlalchemy import select

from app.db.models.aoi import Aoi
from app.db.models.enums import (
    ModelRunStatus,
    Severity,
    TimeMode,
    WorkflowItemStatus,
    WorkflowStatus,
)
from app.db.models.results import ModelRun, ModelScore, WorkflowItem
from app.db.models.stac import StacItem
from app.db.models.thresholds import ThresholdConfig
from app.db.models.workflow import (
    Workflow,
    WorkflowCollection,
    WorkflowModelCollectionConfig,
    WorkflowModelConfig,
)
from worker.celery_app import celery_app
from worker.compat import build_normalized_assets
from worker.db import get_session
from worker.models.registry import get_model
from worker.providers.registry import get_collection

# helpers


def _bbox_from_wkb(geometry_wkb) -> list[float]:
    return list(to_shape(geometry_wkb).bounds)


def _search_stac(provider, col_info, bbox, time_start, time_end, max_cloud_cover):
    client = provider.get_client()
    dt_str = (
        f"{time_start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{time_end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    results = []
    for item in client.search(
        collections=[col_info.slug],
        bbox=bbox,
        datetime=dt_str,
        max_items=200,
    ).items():
        cc = item.properties.get("eo:cloud_cover")
        if max_cloud_cover is None or cc is None or float(cc) <= max_cloud_cover:
            results.append(item)
    return results


def _upsert_stac_item(db, collection_slug: str, stac_item) -> StacItem:
    existing = db.execute(
        select(StacItem).where(
            StacItem.collection_slug == collection_slug,
            StacItem.stac_item_id == stac_item.id,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    dt = stac_item.datetime
    if dt is None:
        dt = datetime.fromisoformat(stac_item.properties.get("datetime", "").replace("Z", "+00:00"))

    row = StacItem(
        collection_slug=collection_slug,
        stac_item_id=stac_item.id,
        geometry=from_shape(shape(stac_item.geometry), srid=4326),
        bbox=list(stac_item.bbox) if stac_item.bbox else None,
        datetime=dt,
        properties=dict(stac_item.properties),
        assets={k: v.to_dict() for k, v in stac_item.assets.items()},
        cloud_cover=stac_item.properties.get("eo:cloud_cover"),
    )
    db.add(row)
    db.flush()
    return row


def _load_band(href: str, aoi_geom_wgs84) -> np.ndarray | None:
    import rasterio
    from rasterio.mask import mask as rio_mask
    from rasterio.warp import transform_geom

    try:
        import planetary_computer

        signed = planetary_computer.sign(href)
    except Exception:
        signed = href

    url = f"/vsicurl/{signed}"
    try:
        with rasterio.open(url) as src:
            aoi_proj = transform_geom("EPSG:4326", src.crs.to_string(), mapping(aoi_geom_wgs84))
            out, _ = rio_mask(src, [aoi_proj], crop=True, nodata=0)
            data = out[0].astype(np.float32)
            nodata = src.nodata if src.nodata is not None else 0
            data[data == nodata] = np.nan
        return data
    except Exception:
        return None


def _apply_threshold(value: float, tc: ThresholdConfig) -> str:
    if tc.green_min <= value <= tc.green_max:
        return Severity.green
    if tc.yellow_min <= value <= tc.yellow_max:
        return Severity.yellow
    return Severity.red


def _severity_rank(s: str | None) -> int:
    return {"green": 0, "yellow": 1, "red": 2}.get(s or "", -1)


# tasks


@celery_app.task(name="worker.tasks.run_workflow", bind=True)
def run_workflow(self, workflow_id: str):
    wf_uuid = uuid.UUID(workflow_id)
    try:
        model_run_ids = []

        with get_session() as db:
            workflow = db.get(Workflow, wf_uuid)
            if workflow is None:
                return

            aoi = db.get(Aoi, workflow.aoi_id)
            bbox = _bbox_from_wkb(aoi.geometry)
            aoi_shape = to_shape(aoi.geometry)
            aoi_filter_mode = workflow.aoi_filter_mode or "intersects"

            # For fixed_future incremental runs, search only from last_checked_at forward
            search_time_start = workflow.time_start
            if workflow.time_mode == TimeMode.fixed_future and workflow.last_checked_at:
                search_time_start = workflow.last_checked_at

            wf_collections = (
                db.execute(
                    select(WorkflowCollection).where(WorkflowCollection.workflow_id == wf_uuid)
                )
                .scalars()
                .all()
            )

            wf_model_configs = (
                db.execute(
                    select(WorkflowModelConfig).where(WorkflowModelConfig.workflow_id == wf_uuid)
                )
                .scalars()
                .all()
            )

            max_cc = None
            for wmc in wf_model_configs:
                m = get_model(wmc.model_slug)
                if m.requirements.max_cloud_cover is not None:
                    cc = m.requirements.max_cloud_cover
                    max_cc = cc if max_cc is None else min(max_cc, cc)

            for wc in wf_collections:
                provider, col_info = get_collection(wc.collection_slug)
                stac_items = _search_stac(
                    provider,
                    col_info,
                    bbox,
                    search_time_start,
                    workflow.time_end,
                    max_cc,
                )

                if aoi_filter_mode == "enclosed":
                    filtered = []
                    for item in stac_items:
                        try:
                            item_shape = shape(item.geometry)
                            if item_shape.area > 0:
                                overlap = item_shape.intersection(aoi_shape).area / item_shape.area
                                if overlap >= 0.8:
                                    filtered.append(item)
                        except Exception:
                            filtered.append(item)
                    stac_items = filtered

                for stac_item in stac_items:
                    db_item = _upsert_stac_item(db, wc.collection_slug, stac_item)

                    existing_wi = db.execute(
                        select(WorkflowItem).where(
                            WorkflowItem.workflow_id == wf_uuid,
                            WorkflowItem.stac_item_id == db_item.id,
                        )
                    ).scalar_one_or_none()
                    if existing_wi:
                        continue

                    wi = WorkflowItem(
                        workflow_id=wf_uuid,
                        stac_item_id=db_item.id,
                        status=WorkflowItemStatus.queued,
                    )
                    db.add(wi)
                    db.flush()

                    for wmc in wf_model_configs:
                        enabled = db.execute(
                            select(WorkflowModelCollectionConfig).where(
                                WorkflowModelCollectionConfig.workflow_model_config_id == wmc.id,
                                WorkflowModelCollectionConfig.collection_slug == wc.collection_slug,
                                WorkflowModelCollectionConfig.is_enabled.is_(True),
                            )
                        ).scalar_one_or_none()
                        if enabled is None:
                            continue

                        mr = ModelRun(
                            workflow_item_id=wi.id,
                            workflow_model_config_id=wmc.id,
                            model_slug=wmc.model_slug,
                            status=ModelRunStatus.queued,
                        )
                        db.add(mr)
                        db.flush()
                        model_run_ids.append(str(mr.id))

            db.commit()

        if model_run_ids:
            chord(group(run_model_for_item.s(mr_id) for mr_id in model_run_ids))(
                finalize_workflow.s(workflow_id)
            )
        else:
            finalize_workflow.apply_async(args=[[], workflow_id])

    except Exception as exc:
        with get_session() as db:
            workflow = db.get(Workflow, wf_uuid)
            if workflow:
                workflow.status = WorkflowStatus.failed
                workflow.error_message = str(exc)
                workflow.completed_at = datetime.now(timezone.utc)
                db.commit()
        raise


@celery_app.task(name="worker.tasks.run_model_for_item")
def run_model_for_item(model_run_id: str) -> str:
    mr_uuid = uuid.UUID(model_run_id)

    with get_session() as db:
        mr = db.get(ModelRun, mr_uuid)
        if mr is None:
            return model_run_id

        mr.status = ModelRunStatus.running
        mr.started_at = datetime.now(timezone.utc)
        db.commit()

        stage = WorkflowItemStatus.fetching
        try:
            wi = db.get(WorkflowItem, mr.workflow_item_id)
            stac_item = db.get(StacItem, wi.stac_item_id)
            wmc = db.get(WorkflowModelConfig, mr.workflow_model_config_id)
            workflow = db.get(Workflow, wi.workflow_id)
            aoi = db.get(Aoi, workflow.aoi_id)

            threshold_configs = {
                tc.score_name: tc
                for tc in db.execute(
                    select(ThresholdConfig).where(
                        ThresholdConfig.workflow_model_config_id == wmc.id
                    )
                )
                .scalars()
                .all()
            }

            model = get_model(wmc.model_slug)
            _, col_info = get_collection(stac_item.collection_slug)
            aoi_geom = to_shape(aoi.geometry)

            stage = WorkflowItemStatus.fetching
            wi.status = stage
            db.commit()

            assets_map = build_normalized_assets(
                stac_item.assets, col_info, model.requirements.required_assets
            )
            bands: dict[str, np.ndarray] = {}
            for norm_name, asset_info in assets_map.items():
                arr = _load_band(asset_info["href"], aoi_geom)
                if arr is None:
                    raise ValueError(f"Could not load band '{norm_name}'")
                bands[norm_name] = arr

            stage = WorkflowItemStatus.scoring
            wi.status = stage
            db.commit()

            output = model.run({"bands": bands, "collection_slug": stac_item.collection_slug})

            # Persist scores
            score_keys = set(model.default_thresholds.keys())
            for score_name, score_value in output.items():
                if score_name not in score_keys or score_value is None:
                    continue
                tc = threshold_configs.get(score_name)
                severity = _apply_threshold(float(score_value), tc) if tc else Severity.green
                db.add(
                    ModelScore(
                        model_run_id=mr.id,
                        score_name=score_name,
                        score_value=float(score_value),
                        is_primary=(score_name == model.primary_score),
                        severity=severity,
                    )
                )

            mr.status = ModelRunStatus.success
            mr.raw_output = output
            mr.completed_at = datetime.now(timezone.utc)
            db.commit()

        except Exception as exc:
            failed_status = (
                WorkflowItemStatus.fetch_failed
                if stage == WorkflowItemStatus.fetching
                else WorkflowItemStatus.score_failed
            )
            wi = db.get(WorkflowItem, mr.workflow_item_id)
            wi.status = failed_status
            mr.status = ModelRunStatus.failed
            mr.error_message = str(exc)[:500]
            mr.completed_at = datetime.now(timezone.utc)
            db.commit()

        # Update WorkflowItem as soon as all its model runs are finished
        _try_finalize_item(mr.workflow_item_id, db)

    return model_run_id


def _try_finalize_item(wi_id: uuid.UUID, db) -> None:
    all_runs = (
        db.execute(select(ModelRun).where(ModelRun.workflow_item_id == wi_id)).scalars().all()
    )

    in_progress = {ModelRunStatus.queued, ModelRunStatus.running}
    if any(mr.status in in_progress for mr in all_runs):
        return  # still waiting on other model runs for this item

    wi = db.get(WorkflowItem, wi_id)
    now = datetime.now(timezone.utc)

    # If item already has a stage-specific failed status, keep it
    already_failed = wi.status in (
        WorkflowItemStatus.fetch_failed,
        WorkflowItemStatus.score_failed,
        WorkflowItemStatus.failed,
    )

    if all(mr.status == ModelRunStatus.failed for mr in all_runs):
        if not already_failed:
            wi.status = WorkflowItemStatus.failed
        wi.processed_at = now
        db.commit()
        return

    worst: str | None = None
    for mr in all_runs:
        for score in (
            db.execute(
                select(ModelScore).where(
                    ModelScore.model_run_id == mr.id,
                    ModelScore.is_primary.is_(True),
                )
            )
            .scalars()
            .all()
        ):
            if _severity_rank(score.severity) > _severity_rank(worst):
                worst = score.severity

    wi.overall_severity = worst
    wi.status = WorkflowItemStatus.processed
    wi.processed_at = now
    db.commit()


@celery_app.task(name="worker.tasks.finalize_workflow")
def finalize_workflow(model_run_results: list, workflow_id: str):
    wf_uuid = uuid.UUID(workflow_id)

    with get_session() as db:
        workflow = db.get(Workflow, wf_uuid)
        if workflow is None:
            return

        items = (
            db.execute(select(WorkflowItem).where(WorkflowItem.workflow_id == wf_uuid))
            .scalars()
            .all()
        )

        total = len(items)
        _failed_statuses = {
            WorkflowItemStatus.failed,
            WorkflowItemStatus.fetch_failed,
            WorkflowItemStatus.score_failed,
        }
        failed = sum(1 for wi in items if wi.status in _failed_statuses)
        processed = sum(1 for wi in items if wi.status == WorkflowItemStatus.processed)

        if total == 0 or (failed == total):
            workflow.status = WorkflowStatus.failed
        elif failed > 0:
            workflow.status = WorkflowStatus.completed_with_errors
        else:
            workflow.status = WorkflowStatus.completed

        workflow.error_message = f"{failed}/{total} items failed" if failed else None
        now = datetime.now(timezone.utc)
        workflow.completed_at = now
        workflow.updated_at = now
        workflow.last_checked_at = now
        db.commit()


@celery_app.task(name="worker.tasks.check_due_workflows")
def check_due_workflows():
    """Periodic task: dispatch run_workflow for due fixed_future monitoring workflows."""
    from app.db.models.workflow import Workflow as WorkflowModel

    now = datetime.now(timezone.utc)

    with get_session() as db:
        candidates = (
            db.execute(
                select(WorkflowModel).where(
                    WorkflowModel.time_mode == TimeMode.fixed_future,
                    WorkflowModel.poll_interval_minutes.isnot(None),
                    WorkflowModel.status.in_(
                        [
                            WorkflowStatus.completed,
                            WorkflowStatus.completed_with_errors,
                            WorkflowStatus.failed,
                        ]
                    ),
                )
            )
            .scalars()
            .all()
        )

        dispatched = []
        for wf in candidates:
            if wf.last_checked_at is None:
                due = True
            else:
                due = (wf.last_checked_at + timedelta(minutes=wf.poll_interval_minutes)) <= now

            if due:
                wf.status = WorkflowStatus.running
                wf.started_at = now
                wf.updated_at = now
                dispatched.append(str(wf.id))

        if dispatched:
            db.commit()

    for wf_id in dispatched:
        run_workflow.delay(wf_id)
