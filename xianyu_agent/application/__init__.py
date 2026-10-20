"""Application-layer services (taskbook R02+).

Services here sit between the HTTP/MCP surfaces and the repositories:
explicitly injected dependencies, no ambient imports of server state,
read paths strictly read-only. ``xianyu_agent.server`` wires them in its
composition root.
"""
