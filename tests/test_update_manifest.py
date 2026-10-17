import pytest

from local_tools.update_manifest import validate_manifest


def _manifest(**asset_overrides):
    asset = {
        "platform": "windows-x64",
        "filename": "RimCrow-v0.24.13-windows.zip",
        "size": 123,
        "sha256": "a" * 64,
        "mirrors": [{"id": "r2", "label": "R2", "url": "https://r2.example/update.zip"}],
    }
    asset.update(asset_overrides)
    return {"schema_version": 1, "product": "RimCrow", "version": "0.24.13", "assets": [asset]}


def test_validate_manifest_accepts_release_manifest():
    assert validate_manifest(_manifest())["product"] == "RimCrow"


@pytest.mark.parametrize("field", ["schema_version", "product", "version", "assets"])
def test_validate_manifest_requires_top_level_fields(field):
    payload = _manifest()
    payload.pop(field)
    with pytest.raises(ValueError):
        validate_manifest(payload)


def test_validate_manifest_rejects_missing_hash_and_duplicate_mirrors():
    with pytest.raises(ValueError, match="SHA-256"):
        validate_manifest(_manifest(sha256=""))
    with pytest.raises(ValueError, match="地址重复"):
        validate_manifest(_manifest(mirrors=[
            {"id": "one", "url": "https://r2.example/update.zip"},
            {"id": "two", "url": "https://r2.example/update.zip/"},
        ]))


def test_validate_manifest_rejects_non_https_url():
    with pytest.raises(ValueError, match="HTTPS"):
        validate_manifest(_manifest(mirrors=[{"id": "r2", "url": "http://r2.example/update.zip"}]))
