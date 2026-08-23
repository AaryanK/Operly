import ast
import unittest
from pathlib import Path


class ModelRuntimeCallsiteBoundaryTests(unittest.TestCase):
    """Production code must enter model execution through the shared runtime."""

    def test_product_code_does_not_construct_provider_routes_or_legacy_clients(self):
        root = Path(__file__).resolve().parents[1]
        allowed_files = {
            Path("packages/business_brain/ollama_client.py"),  # migration-only compatibility facade
        }
        forbidden_imports = {
            ("packages.business_brain.ollama_client", "OllamaClient"),
            ("packages.model_runtime.portfolio", "ModelRoute"),
            ("packages.model_runtime.providers", "model_client_for_route"),
            ("packages.model_runtime.providers", "OpenAICompatibleClient"),
            ("packages.model_runtime.openrouter", "OpenRouterClient"),
        }
        violations = []

        for base in (root / "apps", root / "packages"):
            for path in base.rglob("*.py"):
                relative = path.relative_to(root)
                if str(relative).startswith("packages/model_runtime/") or relative in allowed_files:
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
                except (OSError, SyntaxError) as error:
                    self.fail(f"Could not inspect {relative}: {error}")
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    module = node.module or ""
                    for alias in node.names:
                        if (module, alias.name) in forbidden_imports:
                            violations.append(f"{relative}:{node.lineno} imports {module}.{alias.name}")

        self.assertEqual(
            violations,
            [],
            "Model execution must go through model_for_role/model_chat_client_for_role/Model.infer:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
