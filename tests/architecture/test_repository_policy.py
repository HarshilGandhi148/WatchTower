"""Regression checks for important static architecture guard failure modes."""

from pathlib import Path
from unittest import TestCase

from scripts.check_repository import frontend_violations, python_violations


class ImportBoundaryTests(TestCase):
    def test_absolute_cross_agent_import_is_rejected(self) -> None:
        self.assertTrue(
            python_violations(
                Path("agents/weather/agent.py"), "from agents.congestion.metrics import count"
            )
        )

    def test_relative_cross_agent_import_is_rejected(self) -> None:
        for code in (
            "from ..congestion import metrics",
            "from .. import congestion",
            "from agents import congestion",
        ):
            with self.subTest(code=code):
                self.assertTrue(python_violations(Path("agents/weather/agent.py"), code))

    def test_same_agent_and_shared_imports_are_allowed(self) -> None:
        self.assertEqual(
            python_violations(
                Path("agents/weather/agent.py"),
                "from . import assessment\nfrom shared.contracts import MessageEnvelope",
            ),
            [],
        )

    def test_projection_is_pure_and_validator_can_verify_it(self) -> None:
        for code in (
            "from data import adsb",
            "import agents.congestion",
            "from shared import spacetimedb_adapter",
            "from . import tests",
        ):
            with self.subTest(code=code):
                self.assertTrue(python_violations(Path("projection/aggregate_flow.py"), code))
        self.assertEqual(
            python_violations(
                Path("agents/validation/evidence.py"),
                "from projection.aggregate_flow import compare",
            ),
            [],
        )

    def test_runtime_test_input_is_rejected(self) -> None:
        for source in ("runtime/bureau.py", "data/ingestion.py", "agents/weather/assessment.py"):
            with self.subTest(source=source):
                self.assertTrue(
                    python_violations(Path(source), "from tests.fixtures import payload")
                )

    def test_test_modules_can_import_test_inputs(self) -> None:
        self.assertEqual(
            python_violations(
                Path("agents/weather/tests/test_parsing.py"),
                "from tests.fixtures import payload",
            ),
            [],
        )

    def test_shared_layer_cannot_depend_on_agent_implementation(self) -> None:
        self.assertTrue(python_violations(Path("shared/contracts.py"), "import agents.validation"))

    def test_invalid_runtime_syntax_fails_the_guard(self) -> None:
        self.assertTrue(python_violations(Path("runtime/bureau.py"), "def broken("))


class BrowserConfigurationTests(TestCase):
    def test_only_allowlisted_public_settings_are_accepted(self) -> None:
        self.assertEqual(
            frontend_violations(
                Path("frontend/src/lib/config.ts"),
                "const host = import.meta.env.VITE_SPACETIMEDB_WS_URL;",
            ),
            [],
        )
        self.assertTrue(
            frontend_violations(Path("frontend/.env.example"), "VITE_FLIGHTAWARE_API_KEY=")
        )

    def test_browser_environment_cannot_be_read_outside_config_boundary(self) -> None:
        for code in (
            "const settings = import.meta.env;",
            "const value = import.meta.env[name];",
        ):
            with self.subTest(code=code):
                self.assertTrue(frontend_violations(Path("frontend/src/App.tsx"), code))

    def test_backend_service_tokens_cannot_be_referenced_by_browser_code(self) -> None:
        self.assertTrue(
            frontend_violations(
                Path("frontend/src/lib/connection.ts"),
                'const setting = "SPACETIMEDB_VALIDATION_TOKEN";',
            )
        )
