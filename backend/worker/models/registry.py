from worker.models.base import BaseModel
from worker.models.lst_detector import LSTDetector
from worker.models.ndwi_water_detector import NDWIWaterDetector

_registry: dict[str, BaseModel] = {}


def _register(model: BaseModel) -> None:
    _registry[model.slug] = model


def get_model(slug: str) -> BaseModel:
    if slug not in _registry:
        raise KeyError(f"No model registered with slug '{slug}'")
    return _registry[slug]


def all_models() -> list[BaseModel]:
    return list(_registry.values())


# Register all models
_register(NDWIWaterDetector())
_register(LSTDetector())
