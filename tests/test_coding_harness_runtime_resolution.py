import pytest

from packages.coding_harness.runtime_resolution import RuntimeResolutionError, infer_runtime_profile, validate_runtime_contract
from packages.custom_software.source_bundles import SourceFile, build_bundle


def _bundle(files):
    return build_bundle(
        [SourceFile(path, content.encode("utf-8"), "test") for path, content in files],
        "tenant-1", "app-1", "plan-1", 1, 1, "sha256:" + "0" * 64,
    )


def test_static_web_runtime_is_inferred_from_source_tree():
    bundle = _bundle([
        ("index.html", "<!doctype html><script src='js/app.js'></script>"),
        ("js/app.js", "module.exports={add:(a,b)=>a+b};"),
        ("tests/app.test.js", "const test=require('node:test');const assert=require('node:assert/strict');const {add}=require('../js/app.js');test('add',()=>assert.equal(add(1,1),2));"),
    ])
    assert infer_runtime_profile(bundle) == "static-web-js"
    assert validate_runtime_contract(bundle) == "static-web-js"


def test_manual_browser_console_test_does_not_satisfy_runner_gate():
    bundle = _bundle([
        ("index.html", "<!doctype html><script src='app.js'></script>"),
        ("app.js", "function add(a,b){return a+b}"),
        ("tests/app.test.js", "function runTests(){ if (add(1,1) !== 2) throw new Error('failed'); }"),
    ])
    with pytest.raises(RuntimeResolutionError, match="node:test"):
        validate_runtime_contract(bundle)


def test_tautological_node_test_that_never_loads_app_is_rejected():
    bundle = _bundle([
        ("index.html", "<!doctype html><script src='app.js'></script>"),
        ("app.js", "module.exports={add:(a,b)=>a+b};"),
        ("tests/app.test.js", "const test=require('node:test');const assert=require('node:assert/strict');test('math',()=>assert.equal(1+1,2));"),
    ])
    with pytest.raises(RuntimeResolutionError, match="application JavaScript"):
        validate_runtime_contract(bundle)


def test_static_profile_rejects_third_party_package_dependencies():
    bundle = _bundle([
        ("index.html", "<!doctype html><script src='app.js'></script>"),
        ("app.js", "module.exports={add:(a,b)=>a+b};"),
        ("tests/app.test.js", "const test=require('node:test');require('../app.js');test('loads',()=>{});"),
        ("package.json", '{"dependencies":{"express":"latest"}}'),
    ])
    with pytest.raises(RuntimeResolutionError, match="dependency-free"):
        validate_runtime_contract(bundle)


def test_existing_python_profile_remains_source_driven():
    bundle = _bundle([
        ("app.py", "print('app')"),
        ("build.py", "print('build')"),
        ("test_app.py", "import unittest"),
    ])
    assert validate_runtime_contract(bundle) == "python-stdlib-web"
