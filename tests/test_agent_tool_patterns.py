"""Los permisos de herramientas de un agente admiten prefijos con «*» (todo un MCP) además de nombres exactos."""
from praxeon.agents.definition import AgentDefinition, tool_matches


def test_tool_matches_exact_wildcard_and_prefix():
    assert tool_matches(["*"], "cualquiera")
    assert tool_matches(["read_file"], "READ_FILE")
    assert tool_matches(["mcp__marq__*"], "mcp__marq__pm_list_tasks")
    assert not tool_matches(["mcp__marq__*"], "mcp__otro__pm_list_tasks")
    assert not tool_matches(["mcp__marq__*"], "read_file")
    assert not tool_matches([], "read_file")


def _agent(**kw):
    return AgentDefinition(agent_id="a", name="A", role="Developer", system_prompt="x", **kw)


def test_agent_allows_a_whole_mcp_by_prefix_and_forbidden_wins():
    agent = _agent(allowed_tools=["read_file", "mcp__marq__*"], forbidden_tools=["mcp__marq__pm_delete_task"])
    assert agent.is_tool_allowed("mcp__marq__pm_list_tasks")
    assert not agent.is_tool_allowed("mcp__marq__pm_delete_task")
    assert not agent.is_tool_allowed("mcp__github__create_issue")
    assert agent.is_tool_allowed("read_file")
