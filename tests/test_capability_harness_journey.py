import asyncio

from packages.coding_harness.opencode_agent import OpenCodeStyleCodingAgent
from packages.coding_harness.runtime_resolution import validate_runtime_contract
from packages.custom_software.source_bundles import build_bundle


SPEC = """
Create an interactive decision board. Users can compare options against weighted
criteria, see a ranked leader, and later change ranking behavior. Keep the source
small and dependency-free for the static-web-js runner.
""".strip()


class BuildDecisionBoardModel:
    async def chat(self, messages, tools=None):
        return {
            "role": "assistant",
            "content": "Building the decision board directly in the project workspace.",
            "tool_calls": [
                {
                    "function": {
                        "name": "write",
                        "arguments": {
                            "path": "index.html",
                            "content": "<!doctype html><main><h1>Decision Board</h1><section id='leader' class='leader-card'><strong>Current leader</strong><span id='leader-name'></span></section><script src='app.js'></script></main>",
                        },
                    }
                },
                {
                    "function": {
                        "name": "write",
                        "arguments": {
                            "path": "app.js",
                            "content": "function score(values,weights){return values.reduce((n,v,i)=>n+Number(v)*Number(weights[i]||0),0)}\nfunction compare(a,b,weights){return score(b.values,weights)-score(a.values,weights)}\nif(typeof module!=='undefined')module.exports={score,compare};\n",
                        },
                    }
                },
                {
                    "function": {
                        "name": "write",
                        "arguments": {
                            "path": "tests/app.test.js",
                            "content": "const test=require('node:test');const assert=require('node:assert/strict');const {score,compare}=require('../app.js');test('weighted score',()=>assert.equal(score([2,3],[4,5]),23));test('ranking',()=>assert.ok(compare({values:[1]},{values:[2]},[1])>0));",
                        },
                    }
                },
                {
                    "function": {
                        "name": "finish",
                        "arguments": {"summary": "Created the decision board.", "verification": ["node --test"]},
                    }
                },
            ],
        }


class VisualLeaderEditModel:
    def __init__(self):
        self.turn = 0

    async def chat(self, messages, tools=None):
        self.turn += 1
        if self.turn == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "inspect_visual", "arguments": {}}}],
            }
        if self.turn == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "grep", "arguments": {"query": "leader-card"}}},
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {
                                "path": "index.html",
                                "old": "<section id='leader' class='leader-card'><strong>Current leader</strong>",
                                "new": "<section id='leader' class='leader-card subtle'><span class='leader-badge'>Current leader</span>",
                            },
                        }
                    },
                    {
                        "function": {
                            "name": "finish",
                            "arguments": {"summary": "Reduced visual dominance and kept a subtle leader badge."},
                        }
                    },
                ],
            }
        raise AssertionError("unexpected visual edit turn")


class TieRuleEditModel:
    def __init__(self):
        self.turn = 0

    async def chat(self, messages, tools=None):
        self.turn += 1
        if self.turn == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "read", "arguments": {"path": "app.js"}}},
                    {"function": {"name": "read", "arguments": {"path": "tests/app.test.js"}}},
                ],
            }
        if self.turn == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {
                                "path": "app.js",
                                "old": "if(typeof module!=='undefined')module.exports={score,compare};",
                                "new": "function effectivelyTied(a,b){const hi=Math.max(Math.abs(a),Math.abs(b),1);return Math.abs(a-b)/hi<=0.05}\nif(typeof module!=='undefined')module.exports={score,compare,effectivelyTied};",
                            },
                        }
                    },
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {
                                "path": "tests/app.test.js",
                                "old": "const {score,compare}=require('../app.js');",
                                "new": "const {score,compare,effectivelyTied}=require('../app.js');",
                            },
                        }
                    },
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {
                                "path": "tests/app.test.js",
                                "old": "test('ranking',()=>assert.ok(compare({values:[1]},{values:[2]},[1])>0));",
                                "new": "test('ranking',()=>assert.ok(compare({values:[1]},{values:[2]},[1])>0));test('within five percent is tied',()=>assert.equal(effectivelyTied(100,96),true));test('outside five percent is not tied',()=>assert.equal(effectivelyTied(100,90),false));",
                            },
                        }
                    },
                    {
                        "function": {
                            "name": "finish",
                            "arguments": {"summary": "Added the five-percent effective-tie rule and executable tests."},
                        }
                    },
                ],
            }
        raise AssertionError("unexpected behavior edit turn")


def _bundle(files, version):
    return build_bundle(
        files,
        "workspace-test",
        "decision-board",
        "plan-test",
        1,
        version,
        "sha256:" + "0" * 64,
    )


def test_same_harness_preserves_project_across_build_visual_and_behavior_edits():
    built = asyncio.run(OpenCodeStyleCodingAgent(client=BuildDecisionBoardModel(), max_steps=5).build(SPEC))
    assert validate_runtime_contract(_bundle(built.files, 1)) == "static-web-js"

    visual_context = {
        "selection": {
            "selector": "#leader",
            "tag": "section",
            "text": "Current leader Option A",
            "outerHTML": "<section id='leader' class='leader-card'>...</section>",
            "rect": {"x": 80, "y": 140, "width": 520, "height": 180},
            "computedStyle": {"fontSize": "32px", "fontWeight": "700", "display": "block"},
        },
        "viewport": {"width": 1280, "height": 720},
    }
    visually_edited = asyncio.run(
        OpenCodeStyleCodingAgent(client=VisualLeaderEditModel(), max_steps=6).edit(
            SPEC,
            built.files,
            "Make the selected leader card less dominant and use a subtle Current leader badge.",
            context=visual_context,
        )
    )
    assert visually_edited.changed_paths == ["index.html"]
    assert validate_runtime_contract(_bundle(visually_edited.files, 2)) == "static-web-js"
    html = next(item.content.decode() for item in visually_edited.files if item.path == "index.html")
    assert "leader-badge" in html

    behavior_edited = asyncio.run(
        OpenCodeStyleCodingAgent(client=TieRuleEditModel(), max_steps=6).edit(
            SPEC,
            visually_edited.files,
            "If two option scores are within five percent, report them as effectively tied.",
        )
    )
    assert behavior_edited.changed_paths == ["app.js", "tests/app.test.js"]
    assert validate_runtime_contract(_bundle(behavior_edited.files, 3)) == "static-web-js"

    final_html = next(item.content.decode() for item in behavior_edited.files if item.path == "index.html")
    final_js = next(item.content.decode() for item in behavior_edited.files if item.path == "app.js")
    final_tests = next(item.content.decode() for item in behavior_edited.files if item.path == "tests/app.test.js")
    assert "leader-badge" in final_html
    assert "effectivelyTied" in final_js
    assert "within five percent is tied" in final_tests
