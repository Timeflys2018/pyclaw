"""External integrations namespace.

Holds non-builtin tool sources (MCP, future CLI bridges, sandboxed plugins).
PyClaw's `core/` layer MUST NOT import from this namespace; integration code
attaches to core via duck-typing protocols (e.g., `Tool` Protocol) and the
`external_tool_registrar` callback hook on the agent factory.
"""
