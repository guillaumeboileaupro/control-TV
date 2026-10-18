---
name: local-device-mcp-bridge
description: Design MCP tools that expose an existing local device-control core without embedding an AI client or OpenAI API into the application. Use for ChatGPT-to-local-device integrations, Chromecast controls, tool schemas, authorization boundaries, and low-friction command execution.
---

# Local Device MCP Bridge

Expose a narrow automation surface over an existing local control core. The application remains independently usable without ChatGPT.

## Boundary

- MCP is an adapter around the same core used by the manual application UI.
- Do not embed ChatGPT, an OpenAI client, or an OpenAI API key in the application unless explicitly required by a separate feature.
- Do not create a second device-control implementation for MCP.
- Keep MCP transport, authentication, and reachability separate from device protocol logic.

## Tool design

- Prefer small typed actions with deterministic semantics.
- Use stable device identifiers rather than ambiguous display names internally.
- Return current state after mutating operations when practical.
- Make errors actionable: device unavailable, discovery failed, unsupported media, receiver rejected command, authentication failed, or ambiguous target.
- Avoid conversational logic inside MCP tools; the calling assistant handles natural language.

## Low-friction operation

For safe reversible media controls, design tools so one well-specified request can execute without unnecessary multi-step dialogue. Require clarification only when the requested target or content cannot be resolved safely. Do not invent a device, media URL, application identifier, or successful action.

## Security

- Expose only operations needed by the product.
- Bind local services narrowly by default and document any remote exposure.
- Authenticate remote access when the MCP transport leaves the trusted local boundary.
- Never log tokens, credentials, private URLs, or unnecessary user data.
- Treat content resolvers and URLs as untrusted input.

## Chromecast-style operations

A device-control core may expose capabilities such as discover devices, get status, select target, play resolved media, pause, resume, stop, seek, set volume, mute, and launch supported receiver applications. Only expose operations actually implemented and verified by the core.

## Verification

Test tool schemas independently from real-device integration. For end-to-end claims, verify the MCP call reaches the core and, when hardware is available, that the target device reports the expected resulting state. Clearly distinguish schema tests, simulated tests, and real-device validation.