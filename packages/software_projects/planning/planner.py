"""Requirement-driven Custom Software planning.

New plans are derived from the recursive requirement graph. Historical product
categories and architecture packs are not planning authorities and are not used
to classify new requests.
"""
from copy import deepcopy
import re

from packages.software_projects.planning.recursive_planning import build_recursive_plan
from packages.software_projects.planning.schema import SoftwarePlan

DESIGNS = [
    "editorial",
    "utility",
    "dashboard_led",
    "conversion_focused",
    "image_led",
    "minimal",
    "modular_grid",
    "asymmetric",
]


def _role(identifier, name, permissions, access="authenticated", scope="tenant"):
    return {
        "id": identifier,
        "name": name,
        "description": f"{name} capabilities",
        "permissions": permissions,
        "access": access,
        "dataScope": scope,
    }


def _field(identifier, field_type="string", required=True):
    return {
        "id": identifier,
        "type": field_type,
        "required": required,
        "sensitive": False,
        "options": [],
    }


def _entity(identifier, purpose, roles, lifecycle=None):
    return {
        "id": identifier,
        "name": identifier.replace("_", " ").title(),
        "purpose": purpose,
        "fields": [_field("name")],
        "relationshipIds": [],
        "ownership": "tenant",
        "visibility": roles,
        "lifecycle": list(lifecycle or []),
    }


def _design(prompt):
    text = prompt.lower()
    family = next((item for item in DESIGNS if item.replace("_", " ") in text), None) or "asymmetric"
    return {
        "family": family,
        "visualPersonality": f"{family.replace('_', ' ')} and domain-specific",
        "navigationFamily": "sidebar" if family in {"utility", "dashboard_led"} else "topbar",
        "heroFamily": "split" if family in {"conversion_focused", "image_led"} else "typographic",
        "typographyPairing": "distinct display with accessible grotesk body",
        "typeScale": "fluid modular",
        "contentDensity": "compact" if family in {"utility", "dashboard_led"} else "comfortable",
        "spacingSystem": "8px responsive",
        "gridSystem": "12-column adaptive",
        "surfaceStyle": "tonal sections",
        "cardStyle": "integrated panels",
        "ctaStrategy": "task-priority",
        "mediaStrategy": "domain imagery with functional diagrams",
        "motionStrategy": "reduced-motion-safe feedback",
        "responsiveBehavior": "navigation and grids recompose; no horizontal workflow loss",
        "accessibilityGoals": [
            "WCAG 2.2 AA",
            "keyboard operation",
            "visible focus",
            "semantic landmarks",
        ],
    }


def _explicit_roles(prompt):
    names = []
    role_match = re.search(
        r"(?:roles?(?:\s+exactly)?|preserve(?:\s+these)?\s+roles(?:\s+exactly)?)\s*[:—-]\s*([^\n.]+)",
        prompt,
        re.I,
    )
    if role_match:
        names = [item.strip(" -*") for item in re.split(r"[;,]", role_match.group(1)) if item.strip(" -*")]

    role_block = re.search(r"(?:users and roles|roles)\s*\n+(.*?)(?:\n\s*\n|\n#|\Z)", prompt, re.I | re.S)
    if role_block:
        bullets = [
            re.sub(r"^\s*[-*]\s*", "", line).strip()
            for line in role_block.group(1).splitlines()
            if re.match(r"^\s*[-*]\s+", line)
        ]
        if bullets:
            names = bullets
    return names or ["Administrator", "Authenticated User"]


def build_software_plan(prompt: str) -> SoftwarePlan:
    recursive = build_recursive_plan(prompt)

    role_ids = []
    roles = []
    for name in _explicit_roles(prompt):
        identifier = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if identifier and identifier not in role_ids:
            role_ids.append(identifier)
            roles.append(
                _role(
                    identifier,
                    name,
                    ["workspace:read", "domain:operate"],
                    scope="all" if identifier == "administrator" else "tenant",
                )
            )

    # Persisted concepts come from the requirement graph, not a closed product
    # catalog such as inventory/quotation/field-service templates.
    entity_names = [
        requirement["normalizedMeaning"][:80]
        for requirement in recursive["requirementLedger"]
        if requirement["category"] in {"data model", "persistence component"}
    ] or ["Domain State"]
    entities = [
        _entity(f"domain_record_{index}", name, role_ids)
        for index, name in enumerate(entity_names[:30], 1)
    ]

    leaves = [item for item in recursive["planTree"] if item["status"] == "implementation_ready"]
    capabilities = [
        {
            "id": f"leaf_{index}",
            "category": item["nodeType"],
            "description": item["title"],
            "requirement": item["objective"],
            "implementation": (
                "generate_engine"
                if item["nodeType"] in {"domain engine", "algorithm", "state machine"}
                else "generate_component"
            ),
            "status": "planned",
        }
        for index, item in enumerate(leaves, 1)
    ]
    tests = [test for item in leaves for test in item["requiredTests"]]
    name = re.sub(r"[^A-Za-z0-9 &-]+", " ", prompt).strip().split(".")[0][:80]

    plan = {
        "projectName": name or "Generated Software",
        "summary": prompt.strip(),
        "productCategory": "custom software",
        "targetUsers": [item["name"] for item in roles],
        "businessDomain": "user-defined",
        "primaryGoal": "Construct the requested outcome from validated requirements",
        "successCriteria": [
            "all mandatory requirements mapped",
            "all leaves implementation-ready",
            "global validation passed",
        ],
        "primaryArchitecture": "llm_directed_recursive",
        "secondaryArchitectures": [],
        "implementationMode": "sandbox_generated",
        "confidence": 0.0,
        "rationale": "Architecture is constructed from a recursive requirement graph; no application-type template is selected.",
        "roles": roles,
        "entities": entities,
        "relationships": [],
        "workflows": [],
        "surfaces": [],
        "backendCapabilities": [item["id"] for item in capabilities],
        "integrations": [],
        "design": _design(prompt),
        "runtime": {
            "strategy": "sandbox_generated",
            "reason": "Validated implementation leaves require isolated source generation",
            "primaryPack": None,
            "secondaryPacks": [],
        },
        "securityConstraints": [
            "tenant isolation",
            "least privilege",
            "generated code outside control plane",
            "approval gated by global validation",
        ],
        "unsupportedRequirements": [],
        "risks": ["model provider availability and generated-code verification"],
        "testRequirements": tests,
        "deploymentRequirements": [
            "migration review",
            "preview acceptance",
            "human approval",
            "atomic deployment",
            "rollback",
        ],
        "effectiveRequirements": [prompt.strip()],
        "capabilities": capabilities,
        "architectureNodes": [
            {
                "id": item["id"],
                "nodeType": item["nodeType"],
                "name": item["title"],
                "inputs": item["inputs"],
                "outputs": item["outputs"],
                "invariants": item["constraints"],
                "implementationRequired": True,
            }
            for item in leaves
        ],
        "stack": {
            "frontend": "model-selected",
            "backend": "model-selected",
            "database": "model-selected",
            "runtime": "python-stdlib-web",
            "reasons": ["runtime mechanics selected after validated leaf contracts"],
            "dependencies": [],
        },
        "requirementEvidence": [
            {
                "requirementId": item["id"],
                "requirement": item["normalizedMeaning"],
                "artifactIds": item["relatedArtifactIds"],
                "testIds": item["relatedTestIds"],
                "status": "planned",
            }
            for item in recursive["requirementLedger"]
        ],
        "reusedPrimitives": [],
        "generatedComponents": [item["id"] for item in capabilities],
        "provenance": {
            "originalPrompt": prompt.strip(),
            "revisions": [],
            "generatedPrompts": [],
            "redactionPolicy": "secrets and credentials are never persisted",
        },
        **recursive,
    }

    lower_prompt = prompt.lower()
    for integration in ("whatsapp", "email", "stripe", "slack"):
        if integration in lower_prompt:
            plan["integrations"].append(integration)
    return SoftwarePlan.model_validate(plan)


def revise_plan(current: SoftwarePlan, request: str) -> SoftwarePlan:
    provenance = deepcopy(current.provenance)
    original = provenance.get("originalPrompt", current.summary)
    revisions = [*provenance.get("revisions", []), request.strip()]
    effective_prompt = original + "\nRevision requirements:\n" + "\n".join(revisions)
    updated = build_software_plan(effective_prompt)
    recursive = build_recursive_plan(
        effective_prompt,
        current.planTree[0].version + 1 if current.planTree else 2,
        current.model_dump(),
    )
    data = updated.model_dump()
    data.update(recursive)
    data["summary"] = original
    data["effectiveRequirements"] = [original, *revisions]
    data["provenance"] = {
        **provenance,
        "originalPrompt": original,
        "revisions": revisions,
        "generatedPrompts": provenance.get("generatedPrompts", []),
        "redactionPolicy": "secrets and credentials are never persisted",
    }
    data["rationale"] += f" Structurally regenerated for revision {len(revisions)}."
    return SoftwarePlan.model_validate(data)
