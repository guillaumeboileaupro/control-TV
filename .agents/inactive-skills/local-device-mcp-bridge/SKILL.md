---
name: local-device-mcp-bridge
description: ARCHIVED and inactive in control-TV (MCP was abandoned on 2026-10-12). Design MCP tools that expose an existing local device-control core without embedding an AI client or an OpenAI API in the application. Use only if the owner reopens an MCP integration.
---

# Local device MCP bridge (archived)

Not discovered by either agent here. Kept for an owner decision to reopen MCP.

Expose a narrow automation surface over the existing control core; the application stays usable
without any assistant.

## Boundary

- MCP is an adapter over the same core as the manual UI: one control implementation, never a
  second one for MCP.
- No embedded assistant client or API key in the application.
- MCP transport, authentication and reachability stay separate from device protocol logic.

## Tools

- Small typed actions with deterministic semantics and stable device identifiers.
- Return the current state after a mutating operation when practical.
- Actionable errors: device unavailable, discovery failed, unsupported media, command rejected,
  authentication failed, ambiguous target.
- Conversational logic stays in the calling assistant. One well-specified request may run a safe,
  reversible media control directly; ask only when the target cannot be resolved safely.
- Never invent a device, media URL, application identifier or successful action; expose only
  operations the core implements and has verified.

## Security

Expose only product operations, bind local services narrowly, authenticate any access beyond the
trusted local boundary, never log credentials or private URLs, and treat resolvers and URLs as
untrusted input.

## Verification

Test tool schemas apart from device integration, and keep schema, simulated and real-device
validation distinct.
