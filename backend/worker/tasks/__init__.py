import logging
import os
import shutil
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from celery import chain, chord, group
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
from worker import storage

log = logging.getLogger(__name__)

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


def _load_band(href: str, aoi_geom_wgs84):
    """Download, reproject AOI, and clip a band. Returns (array float32, transform, crs)."""
    import rasterio
    from rasterio.mask import mask as rio_mask
    from rasterio.warp import transform_geom

    try:
        import planetary_computer

        signed = planetary_computer.sign(href)
    except Exception:
        signed = href

    url = f"/vsicurl/{signed}"
    with rasterio.open(url) as src:
        aoi_proj = transform_geom("EPSG:4326", src.crs.to_string(), mapping(aoi_geom_wgs84))
        out, out_transform = rio_mask(src, [aoi_proj], crop=True, nodata=0)
        data = out[0].astype(np.float32)
        nodata = src.nodata if src.nodata is not None else 0
        data[data == nodata] = np.nan
        return data, out_transform, src.crs


def _apply_threshold(value: float, tc: ThresholdConfig) -> str:
    if tc.green_min <= value <= tc.green_max:
        return Severity.green
    if tc.yellow_min <= value <= tc.yellow_max:
        return Severity.yellow
    return Severity.red


def _severity_rank(s: str | None) -> int:
    return {"green": 0, "yellow": 1, "red": 2}.get(s or "", -1)


def _required_bands_for_item(db, wi: WorkflowItem, stac: StacItem) -> list[str]:
    """Union of required normalized band names across enabled model configs for this scene's collection."""
    enabled = (
        db.execute(
            select(WorkflowModelCollectionConfig).where(
                WorkflowModelCollectionConfig.workflow_id == wi.workflow_id,
                WorkflowModelCollectionConfig.collection_slug == stac.collection_slug,
                WorkflowModelCollectionConfig.is_enabled.is_(True),
            )
        )
        .scalars()
        .all()
    )
    bands: set[str] = set()
    for wmc_link in enabled:
        wmc = db.get(WorkflowModelConfig, wmc_link.workflow_model_config_id)
        if wmc is None:
            continue
        model = get_model(wmc.model_slug)
        bands.update(model.requirements.required_assets)
    return sorted(bands)


def _fail_all_model_runs_for_item(db, wi_id: uuid.UUID, message: str) -> None:
    runs = db.execute(select(ModelRun).where(ModelRun.workflow_item_id == wi_id)).scalars().all()
    now = datetime.now(timezone.utc)
    for mr in runs:
        if mr.status in (ModelRunStatus.queued, ModelRunStatus.running):
            mr.status = ModelRunStatus.failed
            mr.error_message = message[:500]
            mr.completed_at = now


# tasks


@celery_app.task(name="worker.tasks.run_workflow", bind=True)
def run_workflow(self, workflow_id: str):
    wf_uuid = uuid.UUID(workflow_id)
    try:
        # (workflow_item_id, [model_run_id, ...]) so we can group by item below.
        item_to_runs: dict[uuid.UUID, list[str]] = defaultdict(list)

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
                        item_to_runs[wi.id].append(str(mr.id))

            db.commit()

        if item_to_runs:
            # Per item: fetch+upload bands once, then score all its model runs in parallel.
            item_pipelines = [
                chain(
                    store_item_bands.si(str(wi_id)),
                    group(score_model_run.si(mr_id) for mr_id in mr_ids),
                )
                for wi_id, mr_ids in item_to_runs.items()
            ]
            chord(group(item_pipelines))(finalize_workflow.s(workflow_id))
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


@celery_app.task(name="worker.tasks.store_item_bands", bind=True)
def store_item_bands(self, workflow_item_id: str) -> str:
    """
    Phase 1: download + clip every required band for this item, stage each to local disk.
    Phase 2: upload each staged COG to MinIO under {workflow_id}/{item_id}/{band}.tif.

    On any failure the item flips to fetch_failed / upload_failed, all of its model runs
    are marked failed, and the exception re-raises so the chained scoring group short-circuits.
    """
    wi_uuid = uuid.UUID(workflow_item_id)
    tmpdir = tempfile.mkdtemp(prefix=f"bands-{wi_uuid}-")

    try:
        with get_session() as db:
            wi = db.get(WorkflowItem, wi_uuid)
            if wi is None:
                return workflow_item_id

            stac = db.get(StacItem, wi.stac_item_id)
            workflow = db.get(Workflow, wi.workflow_id)
            aoi = db.get(Aoi, workflow.aoi_id)
            _, col_info = get_collection(stac.collection_slug)

            required = _required_bands_for_item(db, wi, stac)
            if not required:
                # No enabled models on this collection — nothing to fetch.
                return workflow_item_id

            try:
                assets_map = build_normalized_assets(stac.assets, col_info, required)
            except ValueError as exc:
                wi.status = WorkflowItemStatus.fetch_failed
                wi.error_message = str(exc)[:500]
                _fail_all_model_runs_for_item(db, wi.id, str(exc))
                db.commit()
                raise

            aoi_geom = to_shape(aoi.geometry)
            workflow_id = wi.workflow_id

            # Phase 1: fetching
            wi.status = WorkflowItemStatus.fetching
            db.commit()

            staged: list[tuple[str, str]] = []
            try:
                for band_name, asset in assets_map.items():
                    array, transform, crs = _load_band(asset["href"], aoi_geom)
                    if array is None or array.size == 0:
                        raise ValueError(f"empty array for band '{band_name}'")
                    tmp_path = os.path.join(tmpdir, f"{band_name}.tif")
                    storage.write_cog_to_disk(tmp_path, array, transform, crs)
                    staged.append((band_name, tmp_path))
                    del array
            except Exception as exc:
                with get_session() as db2:
                    wi2 = db2.get(WorkflowItem, wi_uuid)
                    if wi2 is not None:
                        wi2.status = WorkflowItemStatus.fetch_failed
                        wi2.error_message = str(exc)[:500]
                        _fail_all_model_runs_for_item(db2, wi2.id, str(exc))
                        db2.commit()
                raise

            # Phase 2: uploading
            wi.status = WorkflowItemStatus.uploading
            db.commit()

            try:
                for band_name, tmp_path in staged:
                    key = storage.band_key(workflow_id, wi.id, band_name)
                    if storage.band_exists(key):
                        continue  # retry idempotency within this workflow
                    storage.upload_file(key, tmp_path)
            except Exception as exc:
                with get_session() as db2:
                    wi2 = db2.get(WorkflowItem, wi_uuid)
                    if wi2 is not None:
                        wi2.status = WorkflowItemStatus.upload_failed
                        wi2.error_message = str(exc)[:500]
                        _fail_all_model_runs_for_item(db2, wi2.id, str(exc))
                        db2.commit()
                raise

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return workflow_item_id


@celery_app.task(name="worker.tasks.score_model_run")
def score_model_run(model_run_id: str) -> str:
    mr_uuid = uuid.UUID(model_run_id)

    with get_session() as db:
        mr = db.get(ModelRun, mr_uuid)
        if mr is None:
            return model_run_id

        # If the item is already in a failed state (fetch/upload), don't try to score.
        wi = db.get(WorkflowItem, mr.workflow_item_id)
        if wi.status in (
            WorkflowItemStatus.fetch_failed,
            WorkflowItemStatus.upload_failed,
            WorkflowItemStatus.failed,
        ):
            mr.status = ModelRunStatus.skipped
            mr.completed_at = datetime.now(timezone.utc)
            db.commit()
            return model_run_id

        mr.status = ModelRunStatus.running
        mr.started_at = datetime.now(timezone.utc)
        # Flip item to scoring (idempotent across parallel score tasks for the same item).
        if wi.status != WorkflowItemStatus.scoring:
            wi.status = WorkflowItemStatus.scoring
        db.commit()

        try:
            stac = db.get(StacItem, wi.stac_item_id)
            wmc = db.get(WorkflowModelConfig, mr.workflow_model_config_id)

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

            # Read bands from MinIO. transform/crs from the first band are reused for derived rasters.
            bands: dict[str, np.ndarray] = {}
            transform = None
            crs = None
            for band_name in model.requirements.required_assets:
                key = storage.band_key(wi.workflow_id, wi.id, band_name)
                arr, t, c = storage.get_band_array(key)
                bands[band_name] = arr
                if transform is None:
                    transform, crs = t, c

            inputs = {"bands": bands, "collection_slug": stac.collection_slug}
            output = model.run(inputs)

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

            # Derived rasters: write under the same item prefix. Failure to write derived
            # rasters does NOT fail the model run — the scores are already persisted.
            try:
                derived = model.derived_rasters(inputs)
                for name, array in derived.items():
                    key = storage.band_key(wi.workflow_id, wi.id, name)
                    if storage.band_exists(key):
                        continue
                    storage.put_band_cog(key, array, transform, crs)
            except Exception as exc:
                log.warning("derived raster write failed for model_run %s: %s", mr.id, exc)

        except Exception as exc:
            wi = db.get(WorkflowItem, mr.workflow_item_id)
            if wi.status not in (
                WorkflowItemStatus.fetch_failed,
                WorkflowItemStatus.upload_failed,
            ):
                wi.status = WorkflowItemStatus.score_failed
            mr.status = ModelRunStatus.failed
            mr.error_message = str(exc)[:500]
            mr.completed_at = datetime.now(timezone.utc)
            db.commit()

        _try_finalize_item(mr.workflow_item_id, db)

    return model_run_id


def _try_finalize_item(wi_id: uuid.UUID, db) -> None:
    all_runs = (
        db.execute(select(ModelRun).where(ModelRun.workflow_item_id == wi_id)).scalars().all()
    )

    in_progress = {ModelRunStatus.queued, ModelRunStatus.running}
    if any(mr.status in in_progress for mr in all_runs):
        return

    wi = db.get(WorkflowItem, wi_id)
    now = datetime.now(timezone.utc)

    already_failed = wi.status in (
        WorkflowItemStatus.fetch_failed,
        WorkflowItemStatus.upload_failed,
        WorkflowItemStatus.score_failed,
        WorkflowItemStatus.failed,
    )

    successful_runs = [mr for mr in all_runs if mr.status == ModelRunStatus.success]
    if not successful_runs:
        if not already_failed:
            wi.status = WorkflowItemStatus.failed
        wi.processed_at = now
        db.commit()
        return

    worst: str | None = None
    for mr in successful_runs:
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
def finalize_workflow(_results: list, workflow_id: str):
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
            WorkflowItemStatus.upload_failed,
            WorkflowItemStatus.score_failed,
        }
        failed = sum(1 for wi in items if wi.status in _failed_statuses)

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
