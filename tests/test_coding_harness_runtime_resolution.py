import pytest

from packages.runtime_plugins.runtime_resolution import RuntimeResolutionError, infer_runtime_profile, validate_runtime_contract
from packages.software_projects.source_bundle import SourceFile, build_bundle


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


def test_native_website_anchor_does_not_require_a_custom_interaction_contract():
    bundle = _bundle([
        ("index.html", "<!doctype html><a href='/' data-operly-interaction='nav-brand'>Home</a><script src='app.js'></script>"),
        ("app.js", "module.exports={siteName:()=> 'Operly'};"),
        ("app.test.js", "const test=require('node:test');const assert=require('node:assert/strict');const {siteName}=require('./app.js');test('loads',()=>assert.equal(siteName(),'Operly'));"),
        ("operly.interactions.json", '{"schemaVersion":1,"interactions":[{"id":"nav-brand","control":"anchor","event":"click","handler":"unused","operation":"unused","success":"navigates","rejection":"not applicable","stateChange":"not applicable","stateProbe":"not applicable","uiEvidence":"browser navigation","uiProjection":"unused","persistence":"not_applicable","reloadOperation":"not applicable","testId":"legacy-nav","requirementIds":[]}]}'),
    ])
    assert validate_runtime_contract(bundle) == "static-web-js"


def test_native_server_bound_contact_form_does_not_require_js_interaction_contract():
    bundle = _bundle([
        (
            "index.html",
            "<!doctype html><form method='post' action='__OPERLY_FORM_ACTION__'>"
            "<input name='name'><input type='email' name='email'>"
            "<textarea name='message'></textarea><input type='hidden' name='website'>"
            "<button type='submit'>Send</button></form><script src='app.js'></script>",
        ),
        ("app.js", "module.exports={siteName:()=> 'Operly'};"),
        ("app.test.js", "const test=require('node:test');const assert=require('node:assert/strict');const {siteName}=require('./app.js');test('loads',()=>assert.equal(siteName(),'Operly'));"),
    ])
    assert validate_runtime_contract(bundle) == "static-web-js"


def test_css_only_nav_checkbox_is_native_browser_state_not_script_contract():
    bundle = _bundle([
        (
            "index.html",
            "<!doctype html><label for='nav-toggle'>Menu</label><input id='nav-toggle' type='checkbox'>"
            "<nav>Menu items</nav><script src='app.js'></script>",
        ),
        ("app.js", "module.exports={siteName:()=> 'Operly'};"),
        ("app.test.js", "const test=require('node:test');const assert=require('node:assert/strict');const {siteName}=require('./app.js');test('loads',()=>assert.equal(siteName(),'Operly'));"),
    ])
    assert validate_runtime_contract(bundle) == "static-web-js"


def test_plain_text_input_outside_native_form_is_still_rejected_as_unwired():
    bundle = _bundle([
        ("index.html", "<!doctype html><input type='text' placeholder='Search'><script src='app.js'></script>"),
        ("app.js", "module.exports={siteName:()=> 'Operly'};"),
        ("app.test.js", "const test=require('node:test');require('./app.js');test('loads',()=>{});"),
    ])
    with pytest.raises(RuntimeResolutionError, match="data-operly-interaction"):
        validate_runtime_contract(bundle)


def test_button_type_button_inside_native_form_still_requires_script_contract():
    bundle = _bundle([
        (
            "index.html",
            "<!doctype html><form method='post' action='__OPERLY_FORM_ACTION__'>"
            "<input name='name'><button type='button'>Preview</button>"
            "<button type='submit'>Send</button></form><script src='app.js'></script>",
        ),
        ("app.js", "module.exports={siteName:()=> 'Operly'};"),
        ("app.test.js", "const test=require('node:test');require('./app.js');test('loads',()=>{});"),
    ])
    with pytest.raises(RuntimeResolutionError, match="data-operly-interaction"):
        validate_runtime_contract(bundle)


def test_visible_dead_button_is_rejected_even_when_preview_and_unit_test_shape_exist():
    bundle = _bundle([
        ("index.html", "<!doctype html><button>New Customer</button><script src='app.js'></script>"),
        ("app.js", "module.exports={createCustomer:x=>x};"),
        ("app.test.js", "const test=require('node:test');require('./app.js');test('loads',()=>{});"),
    ])
    with pytest.raises(RuntimeResolutionError, match="data-operly-interaction"):
        validate_runtime_contract(bundle)


def test_annotated_control_without_real_handler_is_rejected():
    manifest = '{"schemaVersion":1,"interactions":[{"id":"customer-new","control":"button","event":"click","handler":"handleNewCustomer","operation":"createCustomer","success":"customer created","rejection":"validation shown","stateChange":"customer added","stateProbe":"customerCount","uiEvidence":"row appears","uiProjection":"renderCustomers","persistence":"reload_preserved","reloadOperation":"loadCustomers","testId":"interaction_r_001","requirementIds":["R-001"]}]}'
    bundle = _bundle([
        ("index.html", "<!doctype html><button data-operly-interaction='customer-new'>New Customer</button><script src='app.js'></script>"),
        ("app.js", "module.exports={createCustomer:x=>x};"),
        ("app.test.js", "const test=require('node:test');require('./app.js');test('interaction_r_001 customer-new createCustomer',()=>{});"),
        ("operly.interactions.json", manifest),
    ])
    with pytest.raises(RuntimeResolutionError, match="not wired"):
        validate_runtime_contract(bundle)


def test_fully_traced_interaction_contract_is_accepted():
    manifest = '{"schemaVersion":1,"interactions":[{"id":"customer-new","control":"button","event":"click","handler":"handleNewCustomer","operation":"createCustomer","success":"customer created","rejection":"validation shown","stateChange":"customer added","stateProbe":"customerCount","uiEvidence":"row appears","uiProjection":"renderCustomers","persistence":"reload_preserved","reloadOperation":"loadCustomers","testId":"interaction_r_001","requirementIds":["R-001"]}]}'
    bundle = _bundle([
        ("index.html", "<!doctype html><button data-operly-interaction='customer-new'>New Customer</button><script src='app.js'></script>"),
        ("app.js", "let customers=[]; function createCustomer(s){customers.push({...s,created:true});return customers.at(-1)} function customerCount(){return customers.length} function renderCustomers(){return customers.map(x=>x.created?'row':'').join('')} function loadCustomers(){return customers} function handleNewCustomer(){const result=createCustomer({});renderCustomers();return result} if(typeof document!=='undefined')document.querySelector('[data-operly-interaction=customer-new]').addEventListener('click',handleNewCustomer); module.exports={createCustomer,customerCount,renderCustomers,loadCustomers,handleNewCustomer};"),
        ("app.test.js", "const test=require('node:test');const assert=require('node:assert/strict');const {handleNewCustomer,customerCount,renderCustomers,loadCustomers}=require('./app.js');test('interaction_r_001 customer-new handleNewCustomer createCustomer customerCount renderCustomers loadCustomers',()=>{handleNewCustomer();assert.equal(customerCount(),1);assert.equal(renderCustomers(),'row');assert.equal(loadCustomers().length,1)});"),
        ("operly.interactions.json", manifest),
    ])
    assert validate_runtime_contract(bundle) == "static-web-js"


def test_python_web_preview_with_dead_visible_action_is_rejected():
    bundle = _bundle([
        ("app.py", "PAGE=\"\"\"<!doctype html><button>Record Transaction</button>\"\"\""),
        ("build.py", "print('build')"),
        ("test_app.py", "import unittest\nclass Tests(unittest.TestCase):\n def test_loads(self): self.assertTrue(True)"),
    ])

    with pytest.raises(RuntimeResolutionError, match="data-operly-interaction"):
        validate_runtime_contract(bundle)
