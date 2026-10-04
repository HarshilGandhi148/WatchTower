"""Check static ownership boundaries and public browser configuration.

This guard does not prove reducer authorization or live/projection DB isolation.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_NAMES = frozenset({"weather", "congestion", "validation"})
PYTHON_RUNTIME_ROOTS = ("agents", "data", "projection", "shared", "runtime")
FRONTEND_PUBLIC_ENV = frozenset(
    {"VITE_SPACETIMEDB_WS_URL", "VITE_SPACETIMEDB_DATABASE", "VITE_DISPLAY_TIMEZONE"}
)
REQUIRED_PATHS = (
    "full_technical_plan.md",
    "agents/weather/agent.py",
    "agents/congestion/agent.py",
    "agents/validation/agent.py",
    "shared/contracts.py",
    "shared/spacetimedb_adapter.py",
    "runtime/bureau.py",
    "projection/aggregate_flow.py",
    "backend/module/src/index.ts",
    "frontend/package.json",
    "frontend/src/module_bindings/README.md",
    "tests/fixtures/README.md",
    ".env.example",
    "frontend/.env.example",
    ".github/CODEOWNERS",
    ".github/workflows/ci.yml",
)


def imported_modules(tree: ast.AST, source: Path) -> set[str]:
    """Resolve static absolute and relative import statements, including aliases."""
    modules: set[str] = set()
    package_parts = list(source.with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package_parts) - node.level + 1
                if keep < 0:
                    modules.add("__invalid_relative_import__")
                    continue
                base = ".".join(package_parts[:keep])
                if node.module:
                    base = ".".join(filter(None, (base, node.module)))
            else:
                base = node.module or ""
            if base:
                modules.add(base)
            modules.update(".".join(filter(None, (base, alias.name))) for alias in node.names)
    return modules


def python_violations(source: Path, content: str) -> list[str]:
    """Return violations for one repository-relative Python source file."""
    if "tests" in source.parts or not source.parts:
        return []
    layer = source.parts[0]
    if layer not in PYTHON_RUNTIME_ROOTS:
        return []
    try:
        modules = imported_modules(ast.parse(content, filename=str(source)), source)
    except SyntaxError as error:
        return [f"{source}: invalid Python syntax at line {error.lineno}"]

    violations: list[str] = []
    for module in sorted(modules):
        parts = module.split(".")
        if "tests" in parts or "fixtures" in parts:
            violations.append(f"{source}: runtime must not import test input ({module})")
        if module == "__invalid_relative_import__":
            violations.append(f"{source}: relative import escapes its package")
        if layer == "agents" and len(source.parts) > 1:
            owner = source.parts[1]
            if parts[0] == "agents" and len(parts) > 1 and parts[1] in AGENT_NAMES:
                if parts[1] != owner:
                    violations.append(f"{source}: cross-agent implementation import ({module})")
        blocked: dict[str, set[str]] = {
            "shared": {"agents", "data", "projection", "runtime"},
            "data": {"agents", "projection", "runtime"},
            "projection": {"agents", "data", "runtime"},
        }
        if parts[0] in blocked.get(layer, set()):
            violations.append(f"{source}: reversed layer dependency ({module})")
        if layer == "projection" and module.startswith("shared.spacetimedb_adapter"):
            violations.append(f"{source}: projection must not import DB access ({module})")
    return violations


def frontend_violations(source: Path, content: str) -> list[str]:
    """Enforce public config names and a single browser env-access boundary."""
    violations = [
        f"{source}: unapproved public environment name ({name})"
        for name in sorted(set(re.findall(r"\bVITE_[A-Z0-9_]+\b", content)) - FRONTEND_PUBLIC_ENV)
    ]
    if "import.meta.env" in content and source != Path("frontend/src/lib/config.ts"):
        violations.append(f"{source}: browser env access belongs in src/lib/config.ts")
    if re.search(
        r"\b(?:SPACETIMEDB_\w+_TOKEN|(?:WEATHER|CONGESTION|VALIDATION)_AGENT_SEED|"
        r"ASI_ONE_API_KEY|FLIGHTAWARE_API_KEY|OPENSKY_CLIENT_SECRET)\b",
        content,
    ):
        violations.append(f"{source}: backend secret configuration referenced in browser code")
    return violations


def check(root: Path = ROOT) -> list[str]:
    """Check the scaffold and every runtime source, without external dependencies."""
    violations: list[str] = []
    for required in REQUIRED_PATHS:
        if not (root / required).is_file():
            violations.append(f"Missing required file: {required}")

    agents_dir = root / "agents"
    names = (
        {path.name for path in agents_dir.iterdir() if path.is_dir() and path.name != "__pycache__"}
        if agents_dir.is_dir()
        else set()
    )
    if names != AGENT_NAMES:
        violations.append(f"Expected exactly the three agent folders; found {sorted(names)}")

    for layer in PYTHON_RUNTIME_ROOTS:
        for source in sorted((root / layer).rglob("*.py")):
            relative = source.relative_to(root)
            violations.extend(python_violations(relative, source.read_text(encoding="utf-8")))

    for source in sorted((root / "frontend" / "src").rglob("*")):
        if (
            source.suffix in {".ts", ".tsx", ".js", ".jsx"}
            and "module_bindings" not in source.parts
        ):
            relative = source.relative_to(root)
            violations.extend(frontend_violations(relative, source.read_text(encoding="utf-8")))
    env_template = root / "frontend" / ".env.example"
    if env_template.is_file():
        violations.extend(
            frontend_violations(
                Path("frontend/.env.example"), env_template.read_text(encoding="utf-8")
            )
        )
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("Repository policy passed: three agents, static boundaries, public frontend config.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
