from typing import Any

import numpy as np

from worker.models.base import BaseModel, ModelRequirements, ScoreOutput, ThresholdBand

# Reflectance scaling per collection: (scale, offset)
_REFLECTANCE_SCALING: dict[str, tuple[float, float]] = {
    "landsat-c2-l2": (0.0000275, -0.2),
    "landsat-c2-l1": (0.0000275, -0.2),
    "sentinel-2-l2a": (0.0001, 0.0),
}
_DEFAULT_SCALING = (0.0000275, -0.2)

_WATER_NDWI_THRESHOLD = 0.3
_MIN_VALID_PIXELS = 10


class NDWIWaterDetector(BaseModel):
    slug = "ndwi-water-detector"
    name = "NDWI Water Body Detector"
    description = (
        "Computes the Normalized Difference Water Index "
        "(NDWI = (Green − NIR) / (Green + NIR)) to detect surface water extent "
        "and flag changes relative to configured thresholds."
    )
    requirements = ModelRequirements(
        required_assets=["green", "nir"],
        max_cloud_cover=30.0,
        input_mode="bands",
    )

    primary_score = "ndwi_mean"

    score_outputs = {
        "ndwi_mean": ScoreOutput(
            description="Mean NDWI across all valid pixels in the AOI",
            unit="index",
            value_range=(-1.0, 1.0),
        ),
        "water_fraction": ScoreOutput(
            description="Fraction of valid pixels classified as water (NDWI > 0.3)",
            unit="fraction",
            value_range=(0.0, 1.0),
        ),
    }

    default_thresholds = {
        "ndwi_mean": ThresholdBand(green=(0.3, 1.0), yellow=(0.0, 0.3), red=(-1.0, 0.0)),
        "water_fraction": ThresholdBand(green=(0.5, 1.0), yellow=(0.2, 0.5), red=(0.0, 0.2)),
    }

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        bands = inputs["bands"]
        collection_slug = inputs.get("collection_slug", "")
        scale, offset = _REFLECTANCE_SCALING.get(collection_slug, _DEFAULT_SCALING)

        green = np.where(np.isnan(bands["green"]), np.nan, bands["green"] * scale + offset)
        nir = np.where(np.isnan(bands["nir"]), np.nan, bands["nir"] * scale + offset)

        # Clip to physically valid reflectance range
        green = np.where((green < -0.5) | (green > 1.5), np.nan, green)
        nir = np.where((nir < -0.5) | (nir > 1.5), np.nan, nir)

        valid = ~np.isnan(green) & ~np.isnan(nir)
        n_valid = int(np.sum(valid))

        if n_valid < _MIN_VALID_PIXELS:
            return {"ndwi_mean": None, "water_fraction": None, "valid_pixel_count": n_valid}

        g = green[valid]
        n = nir[valid]
        denom = g + n
        ndwi = np.where(np.abs(denom) > 1e-10, (g - n) / denom, np.nan)
        ndwi_valid = ndwi[~np.isnan(ndwi)]

        if len(ndwi_valid) == 0:
            return {"ndwi_mean": None, "water_fraction": None, "valid_pixel_count": n_valid}

        return {
            "ndwi_mean": round(float(np.mean(ndwi_valid)), 6),
            "water_fraction": round(
                float(np.sum(ndwi_valid > _WATER_NDWI_THRESHOLD) / len(ndwi_valid)), 6
            ),
            "valid_pixel_count": len(ndwi_valid),
        }
