from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.objective import ObjectiveInterpretationError, ObjectiveInterpreter
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    language: str
    scope: str
    prompt: str
    kind: str
    external: bool
    mutation: bool = False
    wait: bool = False
    dispatch: str = "respond"
    capability: str | None = None


CASES: tuple[Case, ...] = (
    # Belgian Dutch / Flemish
    Case("nl.gmail.search", "nl-BE", "personal", "zoek de e-mails van papa over mijn vlucht", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("nl.calendar.list", "nl-BE", "personal", "wat staat er morgen in mijn agenda", "retrieve", True, dispatch="direct_capability", capability="google.calendar.list_events"),
    Case("nl.gmail.send", "nl-BE", "personal", "stuur papa een mail dat ik veilig thuis ben", "act", True, mutation=True, dispatch="direct_capability", capability="google.gmail.send_email"),
    Case("nl.no_tool", "nl-BE", "workspace", "wat is het verschil tussen omzet en winst", "respond", False),

    # French
    Case("fr.gmail.search", "fr-BE", "personal", "cherche les e-mails de mon père au sujet de mon vol", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("fr.calendar.freebusy", "fr-BE", "personal", "est-ce que je suis libre vendredi vers 15 heures", "retrieve", True, dispatch="direct_capability", capability="google.calendar.freebusy"),
    Case("fr.invoice.create", "fr-BE", "workspace", "crée une facture de 500 dollars pour le travail de design, échéance dans 14 jours", "act", True, mutation=True, dispatch="direct_capability", capability="workspace.finance.invoice.create_simple"),
    Case("fr.no_tool", "fr-BE", "workspace", "explique simplement la différence entre flux de trésorerie et bénéfice", "respond", False),

    # German
    Case("de.gmail.search", "de-BE", "personal", "finde die E-Mail von Papa über meinen Flug", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("de.calendar.list", "de-BE", "personal", "welche Termine habe ich morgen", "retrieve", True, dispatch="direct_capability", capability="google.calendar.list_events"),
    Case("de.workflow.disable", "de-BE", "personal", "schalte diesen Workflow aus", "act", True, mutation=True, dispatch="direct_capability", capability="workflow.disable"),
    Case("de.no_tool", "de-BE", "workspace", "erkläre Lagerumschlag in einfachen Worten", "respond", False),

    # Spanish
    Case("es.gmail.search", "es", "personal", "busca el correo de papá sobre mi vuelo", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("es.calendar.create", "es", "personal", "pon una cita con el dentista mañana de 2 a 3", "act", True, mutation=True, dispatch="direct_capability", capability="google.calendar.create_event"),
    Case("es.workspace.search", "es", "workspace", "busca a Acme en este espacio de trabajo", "retrieve", True, dispatch="direct_capability", capability="workspace.search"),
    Case("es.no_tool", "es", "workspace", "dame una estrategia básica de precios para una cafetería pequeña", "respond", False),

    # Portuguese
    Case("pt.gmail.search", "pt", "personal", "procura o email do meu pai sobre a passagem", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("pt.tasks.create", "pt", "personal", "cria uma tarefa para entregar o relatório amanhã", "act", True, mutation=True, dispatch="direct_capability", capability="tasks.create"),
    Case("pt.attention", "pt", "workspace", "o que precisa da minha atenção no negócio hoje", "retrieve", True, dispatch="agent_loop", capability="workspace.attention.list"),
    Case("pt.no_tool", "pt", "workspace", "como uma pequena empresa deve pensar sobre margem bruta", "respond", False),

    # Italian
    Case("it.gmail.search", "it", "personal", "trova l'email di papà sul mio volo", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("it.calendar.list", "it", "personal", "che appuntamenti ho domani mattina", "retrieve", True, dispatch="direct_capability", capability="google.calendar.list_events"),
    Case("it.invoice.create", "it", "workspace", "crea una fattura da 750 dollari con scadenza venerdì prossimo", "act", True, mutation=True, dispatch="direct_capability", capability="workspace.finance.invoice.create_simple"),
    Case("it.no_tool", "it", "workspace", "scrivi un breve messaggio di scuse a un cliente arrabbiato", "respond", False),

    # Nepali / Hindi
    Case("ne.gmail.search", "ne", "personal", "बुवाले मेरो फ्लाइटबारे पठाएको इमेल खोज", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("ne.calendar.list", "ne", "personal", "भोलि मेरो क्यालेन्डरमा के छ", "retrieve", True, dispatch="direct_capability", capability="google.calendar.list_events"),
    Case("ne.no_tool", "ne", "workspace", "सानो व्यवसायमा नाफा र नगद प्रवाहको फरक बुझाइदेऊ", "respond", False),
    Case("hi.gmail.send", "hi", "personal", "पापा को ईमेल भेजो कि मैं सुरक्षित घर पहुँच गया", "act", True, mutation=True, dispatch="direct_capability", capability="google.gmail.send_email"),
    Case("hi.workspace.search", "hi", "workspace", "इस workspace में Acme को खोजो", "retrieve", True, dispatch="direct_capability", capability="workspace.search"),
    Case("hi.no_tool", "hi", "workspace", "छोटे व्यवसाय के लिए gross margin आसान भाषा में समझाओ", "respond", False),

    # East Asian languages
    Case("ja.gmail.search", "ja", "personal", "父がフライトについて送ったメールを探して", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("ja.calendar.list", "ja", "personal", "明日の予定を見せて", "retrieve", True, dispatch="direct_capability", capability="google.calendar.list_events"),
    Case("ja.no_tool", "ja", "workspace", "小さな会社にとってキャッシュフローと利益の違いを説明して", "respond", False),
    Case("ko.gmail.search", "ko", "personal", "아빠가 항공편에 대해 보낸 이메일을 찾아줘", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("ko.invoice.create", "ko", "workspace", "디자인 작업에 대해 500달러 청구서를 만들고 14일 후 만기로 해줘", "act", True, mutation=True, dispatch="direct_capability", capability="workspace.finance.invoice.create_simple"),
    Case("ko.no_tool", "ko", "workspace", "작은 회사가 매주 봐야 할 핵심 지표를 설명해줘", "respond", False),
    Case("zh.gmail.search", "zh", "personal", "找一下爸爸发的关于航班的邮件", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("zh.calendar.freebusy", "zh", "personal", "看看我周五下午三点左右有没有空", "retrieve", True, dispatch="direct_capability", capability="google.calendar.freebusy"),
    Case("zh.no_tool", "zh", "workspace", "解释一下小企业的库存周转率", "respond", False),

    # Arabic
    Case("ar.gmail.search", "ar", "personal", "ابحث عن رسالة أبي عن الرحلة", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("ar.calendar.create", "ar", "personal", "أضف موعد طبيب الأسنان غدًا من الثانية إلى الثالثة", "act", True, mutation=True, dispatch="direct_capability", capability="google.calendar.create_event"),
    Case("ar.no_tool", "ar", "workspace", "اشرح الفرق بين التدفق النقدي والربح لصاحب مشروع صغير", "respond", False),

    # Code-switching / messy global usage
    Case("mixed.ne_en", "mixed", "personal", "dad ko flight वाला email खोज na", "retrieve", True, dispatch="direct_capability", capability="google.gmail.search"),
    Case("mixed.es_en", "mixed", "workspace", "find Acme en este workspace porfa", "retrieve", True, dispatch="direct_capability", capability="workspace.search"),
    Case("mixed.fr_en", "mixed", "personal", "check si je suis free vendredi vers 3pm", "retrieve", True, dispatch="direct_capability", capability="google.calendar.freebusy"),
    Case("mixed.no_tool", "mixed", "workspace", "explique gross margin but keep it super simple", "respond", False),
)


def _context(scope: str) -> ExecutionContext:
    if scope == "personal":
        return ExecutionContext(
            workspace_id=None,
            user_id="eval-user",
            membership_id=None,
            role="personal_owner",
            permissions=PERSONAL_EXECUTION_PERMISSIONS,
            channel="eval",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            conversation_id="multilingual-eval",
            scope_kind=ScopeKind.PERSONAL,
            principal_id="user:eval-user",
            workspace_mode="personal",
        )
    return ExecutionContext(
        workspace_id="eval-workspace",
        user_id="eval-user",
        membership_id="eval-membership",
        role="owner",
        permissions=frozenset(),
        channel="eval",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="multilingual-eval",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:eval-user",
    )


def _registry(scope: str):
    return build_personal_runtime().registry if scope == "personal" else build_workspace_runtime().registry


async def _run_case(interpreter: ObjectiveInterpreter, case: Case) -> dict[str, Any]:
    objective = await interpreter.interpret(message=case.prompt, context=_context(case.scope))
    tool_ids: list[str] = []
    if objective.requires_external_state:
        tool_ids = [
            spec.id
            for spec in _registry(case.scope).search(
                objective.capability_query(),
                context=_context(case.scope),
                effective_only=True,
                limit=12,
            )
        ]

    mismatches: list[str] = []
    if objective.kind.value != case.kind:
        mismatches.append(f"kind:{objective.kind.value}!={case.kind}")
    if objective.requires_external_state != case.external:
        mismatches.append(f"external:{objective.requires_external_state}!={case.external}")
    if objective.requires_mutation != case.mutation:
        mismatches.append(f"mutation:{objective.requires_mutation}!={case.mutation}")
    if objective.requires_future_wait != case.wait:
        mismatches.append(f"wait:{objective.requires_future_wait}!={case.wait}")
    if objective.dispatch_path().value != case.dispatch:
        mismatches.append(f"dispatch:{objective.dispatch_path().value}!={case.dispatch}")
    if case.capability and case.capability not in tool_ids:
        mismatches.append(f"tool_missing:{case.capability}")
    if not case.external and tool_ids:
        mismatches.append("no_tool_case_searched_capabilities")

    return {
        "case_id": case.case_id,
        "language": case.language,
        "scope": case.scope,
        "prompt": case.prompt,
        "passed": not mismatches,
        "mismatches": mismatches,
        "objective": objective.objective,
        "kind": objective.kind.value,
        "operations": [operation.value for operation in objective.operations],
        "resources": list(objective.resource_hints),
        "dispatch": objective.dispatch_path().value,
        "tool_ids": tool_ids,
    }


async def main() -> int:
    interpreter = ObjectiveInterpreter(
        model=OpenAICompatibleAgentModel(),
        settings=AgentRuntimeSettings(enabled=True),
    )
    delay = max(0.0, min(float(os.getenv("OPERLY_MULTILINGUAL_EVAL_INTERVAL_SECONDS", "9")), 60.0))
    results: list[dict[str, Any]] = []
    model_errors = 0

    for index, case in enumerate(CASES):
        try:
            result = await _run_case(interpreter, case)
        except ObjectiveInterpretationError as error:
            model_errors += 1 if error.code == "objective_model_failed" else 0
            result = {
                "case_id": case.case_id,
                "language": case.language,
                "scope": case.scope,
                "prompt": case.prompt,
                "passed": False,
                "mismatches": [f"exception:{error.code}"],
            }
        results.append(result)
        print("MULTILINGUAL_EVAL " + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if index + 1 < len(CASES) and delay:
            await asyncio.sleep(delay)

    passed = sum(1 for result in results if result.get("passed"))
    by_language: dict[str, dict[str, int]] = {}
    for result in results:
        language = str(result["language"])
        stats = by_language.setdefault(language, {"total": 0, "passed": 0})
        stats["total"] += 1
        if result.get("passed"):
            stats["passed"] += 1

    summary = {
        "total": len(results),
        "passed": passed,
        "accuracy": round(passed / len(results), 4) if results else None,
        "model_errors": model_errors,
        "by_language": by_language,
        "failed_case_ids": [result["case_id"] for result in results if not result.get("passed")],
    }
    print("MULTILINGUAL_EVAL_SUMMARY " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
