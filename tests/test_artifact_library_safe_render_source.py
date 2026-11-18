"""Dependency-free guard against HTML parsing of artifact metadata.

This is defense in depth; browser DOM testing is still required before merge.
"""
from pathlib import Path
import re


def _assert_safe_artifact_ui(router: str) -> None:
    ui = router.split('async def artifact_ingress_ui(', 1)[1]
    for required in (
        'recent.replaceChildren()',
        'link.textContent=a.filename',
        'code.textContent=a.artifact_id',
        'encodeURIComponent(a.artifact_id)',
    ):
        assert required in ui, f'Missing safe rendering invariant: {required}'
    for sink in (
        r'\binnerHTML\b', r'\bouterHTML\b', r'\binsertAdjacentHTML\s*\(',
        r'\bdocument\s*\.\s*write(?:ln)?\s*\(',
        r'\bDOMParser\s*\(', r'\bcreateContextualFragment\s*\(',
    ):
        assert not re.search(sink, ui), f'HTML parsing sink detected: {sink}'


def test_artifact_library_uses_text_nodes_for_untrusted_metadata():
    router = (Path(__file__).resolve().parents[1] / 'apps/api/artifact_router.py').read_text()
    _assert_safe_artifact_ui(router)


def test_artifact_library_guard_rejects_alternative_html_sinks():
    safe = """async def artifact_ingress_ui(
recent.replaceChildren();
link.textContent=a.filename;
code.textContent=a.artifact_id;
encodeURIComponent(a.artifact_id);
"""
    _assert_safe_artifact_ui(safe)
    for unsafe in (
        'recent.insertAdjacentHTML("beforeend", a.filename)',
        'document.write(a.filename)',
        'document.writeln(a.filename)',
        'link.outerHTML=a.filename',
        'link.innerHTML=a.filename',
        'new DOMParser().parseFromString(a.filename,"text/html")',
        'range.createContextualFragment(a.filename)',
    ):
        try:
            _assert_safe_artifact_ui(safe + '\n' + unsafe)
        except AssertionError:
            pass
        else:
            raise AssertionError(f'Guard missed unsafe mutation: {unsafe}')
