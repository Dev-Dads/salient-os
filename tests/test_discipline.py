"""quorum_core-style discipline: the verifier package stays stdlib-only and
synchronous. A new dependency or an async def is a test failure, not a code
review comment."""

import ast
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "salienceos"

ALLOWED_TOP_LEVEL_IMPORTS = {
    # stdlib only — extend deliberately, never casually
    "dataclasses",
    "enum",
    "hashlib",
    "hmac",
    "json",
    "os",
    "pathlib",
    "subprocess",
    "unicodedata",  # NFKC-normalize a capability string before the un-grantable-namespace check
                    # (confusable defense for the prohibited offense: reservation, ADR 0004)
    # the package itself
    "salienceos",
}

FORBIDDEN_NODES = (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)


def package_modules():
    return sorted(PACKAGE_ROOT.rglob("*.py"))


class Discipline(unittest.TestCase):
    def test_package_is_not_empty(self):
        self.assertGreater(len(package_modules()), 5)

    def test_no_async_anywhere(self):
        for module in package_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                self.assertNotIsInstance(
                    node, FORBIDDEN_NODES,
                    f"{module}: async construct at line {getattr(node, 'lineno', '?')}",
                )

    def test_stdlib_only_imports(self):
        for module in package_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertIsNotNone(node.module, f"{module}: relative import")
                    names = [node.module]
                for name in names:
                    top = name.split(".")[0]
                    self.assertIn(
                        top, ALLOWED_TOP_LEVEL_IMPORTS,
                        f"{module}: import '{name}' is outside the allowed set",
                    )


if __name__ == "__main__":
    unittest.main()
