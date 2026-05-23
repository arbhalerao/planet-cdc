from worker.providers.base import BaseProvider, CollectionInfo
from worker.providers.planetary_computer import PlanetaryComputerProvider

_providers: dict[str, BaseProvider] = {}


def _register(provider: BaseProvider) -> None:
    _providers[provider.slug] = provider


def get_provider(slug: str) -> BaseProvider:
    if slug not in _providers:
        raise KeyError(f"No provider registered with slug '{slug}'")
    return _providers[slug]


def all_providers() -> list[BaseProvider]:
    return list(_providers.values())


def get_collection(collection_slug: str) -> tuple[BaseProvider, CollectionInfo]:
    """Find which provider owns a collection slug and return both."""
    for provider in _providers.values():
        if collection_slug in provider.collections:
            return provider, provider.collections[collection_slug]
    raise KeyError(f"No provider has collection '{collection_slug}'")


# Register all providers
_register(PlanetaryComputerProvider())
