from pathlib import Path


def test_retired_legacy_studio_package_is_absent():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "packages/studio").exists()


def test_production_code_does_not_import_retired_legacy_studio_package():
    root = Path(__file__).resolve().parents[1]
    retired = "packages.studio"
    offenders = []
    for base in (root / "apps", root / "packages"):
        for path in base.rglob("*.py"):
            if retired in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
