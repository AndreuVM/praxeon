"""Rutas inexistentes: el supervisor las veta y el agente recibe un mensaje explícito (no un 'poda' genérica)."""
import os

import pytest

from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.runtime_service.mission_runtime import (
    _missing_path_feedback,
    _missing_path_from_reasons,
)
from praxeon.server.schemas.action import ProposeActionRequest


@pytest.fixture
def svc(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "real.py").write_text("print('hi')\n", encoding="utf-8")
    service.create_session(
        goal="analiza archivos",
        session_id="s1",
        execution_mode="full_access",
        workspace_root=str(ws),
        metadata={"full_access_authorized_by_operator": True},
    )
    return service


def _propose(svc, tool, args, n):
    return svc.propose_action(
        session_id="s1",
        proposal=ProposeActionRequest(action_id=f"a{n}", tool=tool, operation=tool, arguments=args, thought_rationale="x"),
    )


def test_missing_file_is_vetoed_with_reason(svc):
    resp = _propose(svc, "read_file", {"path": "src/ghost.py"}, 1)
    assert resp.status == "REPLAN"
    assert _missing_path_from_reasons(resp.policy.reason_codes) == "src/ghost.py"


def test_relative_path_resolves_against_workspace_not_cwd(svc):
    assert not os.path.exists(os.path.join(os.getcwd(), "src", "real.py"))
    resp = _propose(svc, "read_file", {"path": "src/real.py"}, 2)
    assert resp.status != "REPLAN"
    assert _missing_path_from_reasons(resp.policy.reason_codes) is None


def test_feedback_tells_agent_path_does_not_exist_and_lists_real_files(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "real.py").write_text("x", encoding="utf-8")
    msg = _missing_path_feedback("src/deep/ghost.py", str(ws))
    assert "NO EXISTE" in msg and "src/deep/ghost.py" in msg
    assert "real.py" in msg  # contenido real del ancestro existente más cercano
    assert "list_dir" in msg


def test_reason_parser_ignores_other_codes():
    assert _missing_path_from_reasons(["LOW_GROUNDED_PROBABILITY (0.05 < 0.35)"]) is None
    assert _missing_path_from_reasons(None) is None
