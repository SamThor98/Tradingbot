from __future__ import annotations

from scripts.web_safe_routes import SAFE_READ_ROUTE_INVENTORY, safe_read_routes


def test_safe_route_inventory_contains_only_read_like_paths() -> None:
    paths = safe_read_routes(include_saas=True)
    assert paths
    for path in paths:
        assert path.startswith("/")
        lowered = path.lower()
        assert "/approve" not in lowered
        assert "/reject" not in lowered
        assert "/delete" not in lowered
        # Live scan POST lives at /api/scan; the catalog GET is a cheap read.
        assert path.rstrip("/") != "/api/scan"
        assert not lowered.startswith("/api/scan/")
        assert "/billing" not in lowered
        assert "/oauth/" not in lowered


def test_saas_route_subset_is_opt_in() -> None:
    shared = SAFE_READ_ROUTE_INVENTORY["shared"]
    saas = SAFE_READ_ROUTE_INVENTORY["saas_only"]
    assert "/api/health/ready" in saas
    assert all(path not in shared for path in saas)
