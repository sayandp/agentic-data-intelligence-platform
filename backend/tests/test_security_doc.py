"""SECURITY.md must describe this code, not a previous version of it.

A threat model that has drifted is worse than none: it tells an operator a
limit is in force under a name that no longer exists, or omits a control that
was since added. These tests are deliberately narrow - they check the FACTS a
reader would act on (env var names, default values, the endpoints named), not
the prose around them.
"""

import ast
import re
from pathlib import Path

import pytest

from app.main import app
from app.security import config as security_config
from app.security.rate_limit import LIMITED_PREFIXES

SECURITY_MD = Path(__file__).resolve().parents[2] / "SECURITY.md"


@pytest.fixture(scope="module")
def document() -> str:
    assert SECURITY_MD.exists(), f"SECURITY.md is missing at {SECURITY_MD}"
    return SECURITY_MD.read_text(encoding="utf-8")


def test_every_documented_env_var_exists_in_the_code(document):
    """A limit documented under a name the code does not read is a limit the
    operator will set and never get."""
    documented = set(re.findall(r"`(MAX_[A-Z_]+|RATE_LIMIT_[A-Z_]+)`", document))
    assert documented, "the limits table should name its env vars"

    source = (Path(security_config.__file__)).read_text(encoding="utf-8")
    for name in documented:
        assert f'"{name}"' in source, f"SECURITY.md documents {name}, which app/security/config.py never reads"


def test_the_documented_defaults_are_the_actual_defaults(document):
    """The numbers in the table are what an operator plans capacity against."""
    assert "200 MB" in document
    assert security_config.MAX_UPLOAD_BYTES == 200 * 1024 * 1024

    assert "2,000,000" in document
    assert security_config.MAX_INGEST_ROWS == 2_000_000

    assert "4,096" in document
    assert security_config.MAX_INGEST_COLUMNS == 4096

    assert "60 / 60s" in document
    assert security_config.RATE_LIMIT_REQUESTS == 60
    assert security_config.RATE_LIMIT_WINDOW_SECONDS == 60


def test_the_document_still_says_there_is_no_authentication(document):
    """The single most important sentence in the file. If authentication is
    ever added, this test failing is the prompt to rewrite §2.1 rather than
    leave a document that understates what the system now does."""
    assert "There is no authentication" in " ".join(document.split())

    routes = {getattr(route, "path", "") for route in app.routes}
    auth_shaped = {r for r in routes if any(word in r for word in ("/login", "/auth", "/token", "/session"))}
    assert not auth_shaped, (
        f"authentication-shaped routes exist ({sorted(auth_shaped)}) but SECURITY.md §2.1 still says there is none"
    )


def test_the_rate_limited_endpoints_are_the_ones_the_document_describes(document):
    """§2.3 says the limiter covers 'mutating and expensive endpoints only'
    and that reads are not limited. If a read prefix is ever added to the
    limiter, that sentence becomes false."""
    assert "mutating and expensive endpoints only" in " ".join(document.split())

    for prefix in LIMITED_PREFIXES:
        assert prefix in {"/ingest", "/sources/upload", "/ask", "/predict", "/export", "/analytics"}, (
            f"{prefix} was added to the limiter; check that SECURITY.md §2.3 still describes it correctly"
        )


def test_the_export_claim_matches_the_only_export_route(document):
    """§2.4's reasoning depends on PPTX being the only export format. An
    added CSV or XLSX export would make that section's conclusion wrong."""
    # Matched against whitespace-collapsed text: the source wraps at 80
    # columns, so a literal multi-word match is a test that fails on
    # reflowing rather than on drift.
    flat = " ".join(document.split())
    assert "only export format is PPTX" in flat

    export_routes = {getattr(route, "path", "") for route in app.routes if "/export" in getattr(route, "path", "")}
    for route in export_routes:
        assert "pptx" in route, (
            f"a non-PPTX export route exists ({route}); SECURITY.md §2.4 concludes that nothing this "
            "system produces can execute a formula, which that route may make false"
        )


# ---- the on-demand claim ----
#
# README, SECURITY.md 2.7 and EVIDENCE.md item 12 all now state that this
# platform validates on demand and never on a cadence. Two assertions keep
# that honest. Both are deliberately cheap and specific: they fail on the
# actual act of adding a scheduler, not on unrelated edits.


SCHEDULING_PACKAGES = (
    "apscheduler",
    "celery",
    "croniter",
    "dramatiq",
    "huey",
    "rq",
    "arq",
    "schedule",
    "watchdog",
    "watchfiles",
    "python-crontab",
)


def test_no_scheduling_dependency_is_declared():
    """A scheduler almost always arrives as a dependency first.

    Checked against requirements.txt rather than installed packages, because
    a direct dependency is a deliberate act and a transitive one is not."""
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    declared = {
        line.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    found = declared & set(SCHEDULING_PACKAGES)
    assert not found, (
        f"a scheduling dependency was added ({sorted(found)}). The platform's on-demand claim is stated in "
        "README (after the ingest example), SECURITY.md 2.7 and EVIDENCE.md item 12 - update all three, "
        "or remove the dependency."
    )


def test_the_ingest_graph_has_exactly_two_entry_points_and_both_are_http_handlers():
    """`invoke_in_background` is the only way the ingest graph runs.

    The modules that IMPORT it are therefore the complete list of places a run
    can start from, and today both are request handlers: POST
    /ingest/{source_id} and POST /approvals/{id}/resolve. A scheduler,
    watcher or timer would have to import it from somewhere that is not a
    router, and that is what this notices.

    Detected by parsing imports rather than by searching for the name. A first
    version of this test looked for "invoke_in_background(" and missed
    approvals.py entirely, because there the function is PASSED as a callback
    (`add_task(invoke_in_background, ...)`) rather than called - which is
    precisely how a scheduler would register it too. Text-matching was the
    wrong instrument for the thing being guarded.

    A legitimate new caller failing this is the intended outcome, not a false
    positive - the same shape as the authentication check above. It asks
    whoever adds one to confirm the docs still describe reality."""
    app_dir = Path(__file__).resolve().parents[1] / "app"

    importers = set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "invoke_in_background" for alias in node.names
            ):
                importers.add(path.relative_to(app_dir).as_posix())

    assert importers == {"routers/ingest.py", "routers/approvals.py"}, (
        f"the set of things that can start an ingest run changed: {sorted(importers)}. "
        "If a scheduler, watcher or timer was added, README (after the ingest example), "
        "SECURITY.md 2.7 and EVIDENCE.md item 12 all claim the platform never re-ingests on a "
        "cadence, and SECURITY.md 2.6's SSRF bound depends on it. Update them."
    )


def test_the_documents_state_the_on_demand_limitation(document):
    """The claim must actually be present to be worth guarding."""
    flat = " ".join(document.split())
    assert "There is no scheduled or continuous monitoring" in flat

    readme = " ".join((Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8").split())
    assert "Validation is on demand." in readme

    evidence = " ".join((Path(__file__).resolve().parents[2] / "docs/EVIDENCE.md").read_text(encoding="utf-8").split())
    assert "Nothing runs on a schedule." in evidence
