from pathlib import Path


SOLUTIONS_PAGE = Path("apps/web/src/workspace/SolutionsPage.tsx")


def test_solution_compose_keeps_form_reference_across_awaits():
    source = SOLUTIONS_PAGE.read_text(encoding="utf-8")
    assert "const formElement = event.currentTarget;" in source
    assert "formElement.reset();" in source
    assert "event.currentTarget.reset();" not in source


def test_solution_compose_has_synchronous_duplicate_submit_guard():
    source = SOLUTIONS_PAGE.read_text(encoding="utf-8")
    assert "const composeInFlight = useRef(false);" in source
    assert "if (composeInFlight.current) return;" in source
    assert "composeInFlight.current = true;" in source
    assert "composeInFlight.current = false;" in source
