from pathlib import Path


HISTORICAL_PACKAGE_PATHS = (
    "packages/custom_software",
    "packages/coding_harness",
    "packages/studio",
    "packages/application_builder",
    "packages/dashboard_studio",
)
HISTORICAL_IMPORTS = tuple(value.replace("/", ".") for value in HISTORICAL_PACKAGE_PATHS)


def test_historical_software_packages_are_physically_removed():
    for value in HISTORICAL_PACKAGE_PATHS:
        path = Path(value)
        assert not path.exists(), f"historical package still exists: {path}"


def test_canonical_software_runtime_packages_exist():
    required = (
        Path("packages/software_projects/planning"),
        Path("packages/software_projects/coding"),
        Path("packages/runtime_plugins/runner_contracts.py"),
        Path("packages/runtime_plugins/runner_service.py"),
        Path("packages/runtime_plugins/interaction_contracts.py"),
        Path("packages/agents/runtime.py"),
    )
    for path in required:
        assert path.exists(), f"canonical runtime path missing: {path}"


def test_python_source_and_tests_have_no_historical_implementation_imports():
    offenders = []
    this_file = Path(__file__).resolve()
    for root in (Path("apps"), Path("packages"), Path("tests")):
        for path in root.rglob("*.py"):
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8")
            for token in HISTORICAL_IMPORTS:
                if token in text:
                    offenders.append(f"{path}: {token}")
    assert not offenders, "historical implementation imports remain:\n" + "\n".join(offenders)


def test_workflows_do_not_reference_historical_package_paths():
    offenders = []
    for path in Path(".github/workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for token in HISTORICAL_PACKAGE_PATHS:
            if token in text:
                offenders.append(f"{path}: {token}")
    assert not offenders, "historical CI package paths remain:\n" + "\n".join(offenders)


def test_generation_worker_uses_canonical_owners():
    text = Path("packages/solutions/generation_worker.py").read_text(encoding="utf-8")
    assert "packages.software_projects.planning" in text
    assert "packages.software_projects.coding.execution_loop" in text
    assert "packages.runtime_plugins.runner_adapters" in text
    assert "packages.custom_software" not in text
    assert "packages.coding_harness" not in text


def test_runtime_plugins_own_runner_and_interaction_contracts():
    text = Path("packages/runtime_plugins/builtins.py").read_text(encoding="utf-8")
    assert "packages.runtime_plugins.interaction_contracts" in text
    assert "packages.runtime_plugins.runner_contracts" in text
    assert "packages.runtime_plugins.runtime_profiles" in text
