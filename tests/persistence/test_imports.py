"""Smoke test: persistence and services packages can be imported."""


def test_persistence_package_importable():
    import lore.persistence  # noqa: F401


def test_services_package_importable():
    import lore.services  # noqa: F401
