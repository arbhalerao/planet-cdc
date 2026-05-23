from typing import Any

import numpy as np

from worker.models.base import BaseModel, ModelRequirements, ScoreOutput, ThresholdBand

_SCALE = 0.00341802
_OFFSET = 149.0
_KELVIN_TO_C = 273.15
_HOT_THRESHOLD_C = 35.0
_MIN_VALID = 10


class LSTDetector(BaseModel):
    slug = "lst-detector"
    name = "Land Surface Temperature Detector"
    description = (
        "Derives land surface temperature (LST) from Landsat thermal infrared imagery "
        "(Band 10 / LWIR11). Reports mean surface temperature and the fraction of pixels "
        "exceeding a configurable heat threshold."
    )
    requirements = ModelRequirements(
        required_assets=["thermal1"],
        max_cloud_cover=20.0,
        input_mode="bands",
    )

    primary_score = "lst_mean"

    score_outputs = {
        "lst_mean": ScoreOutput(
            description="Mean land surface temperature across valid pixels",
            unit="°C",
            value_range=(-20.0, 70.0),
        ),
        "hot_fraction": ScoreOutput(
            description=f"Fraction of valid pixels above {_HOT_THRESHOLD_C}°C",
            unit="fraction",
            value_range=(0.0, 1.0),
        ),
    }

    default_thresholds = {
        "lst_mean": ThresholdBand(green=(-20.0, 30.0), yellow=(30.0, 40.0), red=(40.0, 70.0)),
        "hot_fraction": ThresholdBand(green=(0.0, 0.1), yellow=(0.1, 0.3), red=(0.3, 1.0)),
    }

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        raw = inputs["bands"]["thermal1"]

        # Landsat C2 L2 thermal scaling -> Kelvin -> Celsius
        temp_c = np.where(np.isnan(raw), np.nan, raw * _SCALE + _OFFSET - _KELVIN_TO_C)
        # Clip physically implausible values
        temp_c = np.where((temp_c < -80.0) | (temp_c > 100.0), np.nan, temp_c)

        valid = ~np.isnan(temp_c)
        n_valid = int(np.sum(valid))

        if n_valid < _MIN_VALID:
            return {"lst_mean": None, "hot_fraction": None, "valid_pixel_count": n_valid}

        vals = temp_c[valid]
        return {
            "lst_mean": round(float(np.mean(vals)), 4),
            "hot_fraction": round(float(np.sum(vals > _HOT_THRESHOLD_C) / len(vals)), 6),
            "valid_pixel_count": n_valid,
        }
