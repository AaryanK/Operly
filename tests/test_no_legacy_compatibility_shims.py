from pathlib import Path


def test_studio_hardening_compatibility_entrypoint_is_removed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "packages/studio/hardening_policy.py").exists()
    assert (root / "packages/studio/hardening_policy_v2.py").is_file()


def test_production_code_does_not_import_retired_studio_hardening_alias():
    root = Path(__file__).resolve().parents[1]
    retired = "packages.studio.hardening_policy"
    offenders = []
    for base in (root / "apps", root / "packages"):
        for path in base.rglob("*.py"):
            if path.name == "hardening_policy_v2.py":
                continue
            if retired in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
