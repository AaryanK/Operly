from packages.coding_harness.runtime_resolution import RuntimeResolutionError, infer_runtime_profile, validate_runtime_contract
from packages.custom_software.source_bundles import SourceFile, build_bundle


def _bundle(files):
    return build_bundle(
        [SourceFile(path, content.encode("utf-8"), "test") for path, content in files],
        "tenant-1",
        "app-1",
        "plan-1",
        1,
        1,
        "sha256:" + "0" * 64,
    )


def test_static_web_runtime_is_inferred_from_source_tree():
    bundle = _bundle([
        ("index.html", "<!doctype html><script type='module' src='js/app.js'></script>"),
        ("js/app.js", "export const add = (a, b) => a + b;"),
        ("tests/app.test.js", "import test from 'node:test'; import assert from 'node:assert/strict'; test('add', () => assert.equal(1 + 1, 2));"),
    ])
    assert infer_runtime_profile(bundle) == "static-web-js"
    assert validate_runtime_contract(bundle) == "static-web-js"


def test_manual_browser_console_test_does_not_satisfy_runner_gate():
    bundle = _bundle([
        ("index.html", "<!doctype html><script src='app.js'></script>"),
        ("app.js", "function add(a,b){return a+b}"),
        ("tests/app.test.js", "function runTests(){ if (add(1,1) !== 2) throw new Error('failed'); }"),
    ])
    assert infer_runtime_profile(bundle) == "static-web-js"
    try:
        validate_runtime_contract(bundle)
    except RuntimeResolutionError as error:
        assert "node:test" in str(error)
    else:
        raise AssertionError("manual browser test must not pass the isolated-runner gate")


def test_existing_python_profile_remains_source_driven():
    bundle = _bundle([
        ("app.py", "print('app')"),
        ("build.py", "print('build')"),
        ("test_app.py", "import unittest"),
    ])
    assert validate_runtime_contract(bundle) == "python-stdlib-web"
