from pathlib import Path


def test_solutions_page_surfaces_generation_failure_and_retry() -> None:
    source = Path("apps/web/src/workspace/SolutionsPage.tsx").read_text()
    assert "object(solution.generation)" in source
    assert "generation.error" in source
    assert "generation.stage" in source
    assert "retry-generation" in source
    assert "Retry generation" in source
