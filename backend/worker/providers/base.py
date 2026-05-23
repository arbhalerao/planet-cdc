from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BandInfo:
    normalized_name: str  # platform-independent name, e.g. "nir", "swir1"
    asset_key: str  # STAC item assets dict key, e.g. "nir08", "swir16"
    description: str = ""


@dataclass
class CollectionInfo:
    slug: str
    display_name: str
    description: str
    processing_level: str  # "SR", "TOA", etc.
    sensor_type: str  # "multispectral", "sar", etc.
    resolution_m: float
    cloud_cover_property: str | None  # STAC properties key for cloud cover
    bands: list[BandInfo] = field(default_factory=list)

    def band(self, normalized_name: str) -> BandInfo | None:
        return next((b for b in self.bands if b.normalized_name == normalized_name), None)


class BaseProvider(ABC):
    slug: str
    name: str
    stac_api_url: str
    collections: dict[str, CollectionInfo]  # slug -> CollectionInfo

    @abstractmethod
    def get_client(self):
        """Return an authenticated pystac_client.Client instance."""
        ...
