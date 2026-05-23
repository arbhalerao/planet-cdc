from fastapi import APIRouter, HTTPException, status

from app.schemas.catalogue import (
    BandResponse,
    CollectionResponse,
    CompatibilityResponse,
    ModelResponse,
    ScoreOutputResponse,
    ThresholdBandResponse,
)
from worker.compat import check_compatibility
from worker.models.base import BaseModel as WorkerModel
from worker.models.registry import all_models, get_model
from worker.providers.base import BaseProvider, CollectionInfo
from worker.providers.registry import all_providers, get_collection

router = APIRouter(tags=["catalogue"])


def _collection_response(provider: BaseProvider, info: CollectionInfo) -> CollectionResponse:
    return CollectionResponse(
        slug=info.slug,
        display_name=info.display_name,
        description=info.description,
        processing_level=info.processing_level,
        sensor_type=info.sensor_type,
        resolution_m=info.resolution_m,
        cloud_cover_property=info.cloud_cover_property,
        bands=[
            BandResponse(
                normalized_name=b.normalized_name,
                asset_key=b.asset_key,
                description=b.description,
            )
            for b in info.bands
        ],
        provider_slug=provider.slug,
        provider_name=provider.name,
    )


def _model_response(model: WorkerModel) -> ModelResponse:
    compatible_collections: dict[str, CompatibilityResponse] = {}
    for provider in all_providers():
        for col_info in provider.collections.values():
            result = check_compatibility(model, col_info)
            compatible_collections[col_info.slug] = CompatibilityResponse(
                level=result.level,
                reasons=[r.message for r in result.reasons],
            )

    return ModelResponse(
        slug=model.slug,
        name=model.name,
        description=model.description,
        primary_score=model.primary_score,
        required_bands=model.requirements.required_assets,
        max_cloud_cover=model.requirements.max_cloud_cover,
        input_mode=model.requirements.input_mode,
        score_outputs={
            name: ScoreOutputResponse(
                description=so.description,
                unit=so.unit,
                value_range=so.value_range,
            )
            for name, so in model.score_outputs.items()
        },
        compatible_collections=compatible_collections,
        default_thresholds={
            name: ThresholdBandResponse(
                green=t.green,
                yellow=t.yellow,
                red=t.red,
            )
            for name, t in model.default_thresholds.items()
        },
    )


@router.get("/collections", response_model=list[CollectionResponse])
def list_collections():
    result = []
    for provider in all_providers():
        for info in provider.collections.values():
            result.append(_collection_response(provider, info))
    return result


@router.get("/collections/{slug}", response_model=CollectionResponse)
def get_collection_by_slug(slug: str):
    try:
        provider, info = get_collection(slug)
        return _collection_response(provider, info)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Collection '{slug}' not found"
        )


@router.get("/models", response_model=list[ModelResponse])
def list_models():
    return [_model_response(m) for m in all_models()]


@router.get("/models/{slug}", response_model=ModelResponse)
def get_model_by_slug(slug: str):
    try:
        return _model_response(get_model(slug))
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Model '{slug}' not found"
        )
