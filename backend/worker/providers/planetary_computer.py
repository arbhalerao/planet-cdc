from worker.providers.base import BandInfo, BaseProvider, CollectionInfo

_SENTINEL2_BANDS = [
    BandInfo("coastal", "B01", "Coastal Aerosol (60m)"),
    BandInfo("blue", "B02", "Blue (10m)"),
    BandInfo("green", "B03", "Green (10m)"),
    BandInfo("red", "B04", "Red (10m)"),
    BandInfo("rededge1", "B05", "Red Edge 1 (20m)"),
    BandInfo("rededge2", "B06", "Red Edge 2 (20m)"),
    BandInfo("rededge3", "B07", "Red Edge 3 (20m)"),
    BandInfo("nir", "B08", "Near Infrared (10m)"),
    BandInfo("nir08", "B8A", "Narrow NIR (20m)"),
    BandInfo("swir1", "B11", "SWIR 1 (20m)"),
    BandInfo("swir2", "B12", "SWIR 2 (20m)"),
]

_LANDSAT_BANDS = [
    BandInfo("coastal", "coastal", "Coastal/Aerosol (Band 1)"),
    BandInfo("blue", "blue", "Blue (Band 2)"),
    BandInfo("green", "green", "Green (Band 3)"),
    BandInfo("red", "red", "Red (Band 4)"),
    BandInfo("nir", "nir08", "Near Infrared (Band 5)"),
    BandInfo("swir1", "swir16", "Short-wave Infrared 1 (Band 6)"),
    BandInfo("swir2", "swir22", "Short-wave Infrared 2 (Band 7)"),
    BandInfo("thermal1", "lwir11", "Thermal Infrared 1 (Band 10)"),
]


class PlanetaryComputerProvider(BaseProvider):
    slug = "planetary-computer"
    name = "Microsoft Planetary Computer"
    stac_api_url = "https://planetarycomputer.microsoft.com/api/stac/v1"

    collections = {
        "landsat-c2-l2": CollectionInfo(
            slug="landsat-c2-l2",
            display_name="Landsat Collection 2 Level-2",
            description=(
                "Landsat Collection 2 surface reflectance and surface temperature "
                "science products produced by USGS."
            ),
            processing_level="SR",
            sensor_type="multispectral",
            resolution_m=30.0,
            cloud_cover_property="eo:cloud_cover",
            bands=_LANDSAT_BANDS,
        ),
        "landsat-c2-l1": CollectionInfo(
            slug="landsat-c2-l1",
            display_name="Landsat Collection 2 Level-1",
            description=(
                "Landsat Collection 2 top-of-atmosphere radiance products produced by USGS."
            ),
            processing_level="TOA",
            sensor_type="multispectral",
            resolution_m=30.0,
            cloud_cover_property="eo:cloud_cover",
            bands=_LANDSAT_BANDS,
        ),
        "sentinel-2-l2a": CollectionInfo(
            slug="sentinel-2-l2a",
            display_name="Sentinel-2 Level-2A",
            description=(
                "ESA Copernicus Sentinel-2 surface reflectance product, "
                "atmospherically corrected to bottom-of-atmosphere reflectance."
            ),
            processing_level="SR",
            sensor_type="multispectral",
            resolution_m=10.0,
            cloud_cover_property="eo:cloud_cover",
            bands=_SENTINEL2_BANDS,
        ),
    }

    def get_client(self):
        import pystac_client

        return pystac_client.Client.open(self.stac_api_url)
