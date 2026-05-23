from dataclasses import dataclass, field

from worker.models.base import BaseModel
from worker.providers.base import CollectionInfo


@dataclass
class CompatReason:
    code: str
    message: str


@dataclass
class CompatibilityResult:
    compatible: bool
    level: str  # "full" | "partial" | "incompatible"
    reasons: list[CompatReason] = field(default_factory=list)


def check_compatibility(model: BaseModel, collection: CollectionInfo) -> CompatibilityResult:
    reasons: list[CompatReason] = []

    missing = [a for a in model.requirements.required_assets if collection.band(a) is None]
    if missing:
        reasons.append(
            CompatReason(
                code="missing_bands",
                message=f"Required band(s) not available: {', '.join(missing)}",
            )
        )
        return CompatibilityResult(compatible=False, level="incompatible", reasons=reasons)

    if model.requirements.max_cloud_cover is not None and collection.cloud_cover_property is None:
        reasons.append(
            CompatReason(
                code="no_cloud_cover",
                message="Collection does not report cloud cover; cloud filtering will be skipped",
            )
        )

    if reasons:
        return CompatibilityResult(compatible=True, level="partial", reasons=reasons)
    return CompatibilityResult(compatible=True, level="full")


def build_normalized_assets(
    stac_assets: dict,
    collection: CollectionInfo,
    required: list[str],
) -> dict[str, dict]:
    """
    Returns {normalized_name: {"href": ..., "asset_key": ...}} for each name in required.
    Raises ValueError if a band or its asset is missing.
    """
    result: dict[str, dict] = {}
    for norm_name in required:
        band_info = collection.band(norm_name)
        if band_info is None:
            raise ValueError(f"Band '{norm_name}' not in collection '{collection.slug}'")
        asset = stac_assets.get(band_info.asset_key)
        if asset is None:
            raise ValueError(f"Asset '{band_info.asset_key}' missing from STAC item")
        result[norm_name] = {"href": asset["href"], "asset_key": band_info.asset_key}
    return result
