from packages.custom_software.planning_orchestrator import _is_operly_internal_question


def test_operly_internal_architecture_questions_are_not_owner_questions():
    assert _is_operly_internal_question(
        "What constitutes an 'important architectural decision' that requires a query rather than a guess?"
    )
    assert _is_operly_internal_question(
        "What is the specific technical interface or protocol required for OPERLY to interact with the underlying capability?"
    )
    assert _is_operly_internal_question(
        "What constitutes a 'necessary' third-party API in the context of this project?"
    )


def test_material_product_placement_question_still_reaches_owner():
    assert not _is_operly_internal_question(
        "Should this capability be added to the existing customer website or created as a private internal tool?"
    )


def test_business_definition_question_still_reaches_owner():
    assert not _is_operly_internal_question(
        "What constitutes a qualified lead for this business?"
    )
