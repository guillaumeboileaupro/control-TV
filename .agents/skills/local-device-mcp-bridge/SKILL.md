---
name: local-device-mcp-bridge
description: Design MCP tools that expose an existing local device-control core without embedding an AI client or OpenAI API into the application. Use for ChatGPT-to-local-device integrations, Chromecast controls, tool schemas, authorization boundaries, and low-friction command execution.
---

# Local Device MCP Bridge

Expose a narrow automation surface over an existing local control core. The application remains independently usable without ChatGPT.

## Boundary
- MCP is an adapter around the same core used by the manual application UI.
- Keep MCP transport, authentication, and reachability separate from device protocol logic.
- Keep one device-control implementation shared by GUI and MCP.

## Tool design
- Prefer small typed actions with deterministic semantics.
- Use stable device identifiers internally.
- Return current state after mutating operations when practical.
- Make errors actionable.
- Keep conversational logic in the calling assistant.

## Security
Expose only product operations, bind local services narrowly by default, authenticate remote access, protect credentials, and treat URLs/resolvers as untrusted input.

## Verification
Test tool schemas independently from real-device integration and distinguish schema, simulated, and real-device validation.