from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelRequirements:
    required_assets: list[str]  # normalized band names, e.g. ["green", "nir"]
    max_cloud_cover: float | None = None
    input_mode: str = "bands"  # how inputs are packaged: "bands" = {norm_name: ndarray}


@dataclass
class ScoreOutput:
    description: str
    unit: str = ""  # e.g. "index", "fraction", "count"
    value_range: tuple[float, float] = (-1.0, 1.0)


@dataclass
class ThresholdBand:
    green: tuple[float, float]  # (min, max)
    yellow: tuple[float, float]
    red: tuple[float, float]


class BaseModel(ABC):
    slug: str
    name: str
    description: str
    requirements: ModelRequirements
    primary_score: str
    score_outputs: dict[
        str, ScoreOutput
    ]  # score_name -> output metadata; keys match default_thresholds
    default_thresholds: dict[str, ThresholdBand]
    derived_raster_names: list[str] = []  # names of rasters produced by derived_rasters()

    @abstractmethod
    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Receive inputs packaged according to requirements.input_mode.
        For "bands": inputs["bands"][normalized_band_name] = float32 ndarray (NaN = nodata).
        Return a flat dict; keys matching default_thresholds are scored, others are metadata.
        """
        ...

    def derived_rasters(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Optional per-pixel rasters this model produces (e.g. an NDWI map, an LST map).
        Each value is a float32 ndarray with the same shape and georeferencing as the
        input bands. Returned mapping: {raster_name: ndarray}. Default: none.
        """
        return {}
