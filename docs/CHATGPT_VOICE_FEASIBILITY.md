# ChatGPT Voice and control-TV tools: feasibility (gate G1, S0)

Question (issue #34, `DEVELOPMENT_PLAN.md` item O): can **native ChatGPT Voice** call control-TV's custom MCP tools? This file gives the provisional verdict from official documentation (2026-10-08), the diagnostic-only server that settles it empirically, and the exact test and evidence protocol, aligned with Codex's independent audit (issue #34, 2026-10-08). Nothing here sends a command to a TV, and nothing here claims that Voice can call MCP tools before an experiment shows it.

## Provisional verdict (2026-10-08, before any experiment)

**INDETERMINATE.** Official documentation establishes custom MCP servers in ChatGPT and, separately, ChatGPT Voice; it does not establish that a custom MCP tool can be invoked from a native Voice conversation. Only the experiment below can classify it.

| Question | What the official documentation says | Classification |
|---|---|---|
| Custom MCP tools in ChatGPT text (chat) | Supported; set up and used on **ChatGPT on the web**; OAuth, no-auth or mixed authentication; Secure MCP Tunnel for a private server; tools without `readOnlyHint` treated as write actions and confirmed by default | compatible under conditions (account, workspace, plan); not yet demonstrated here |
| Custom MCP tools on mobile (Android, iOS) | MCP apps answered "No - web only" in OpenAI's help article (cited by Codex's review; the page was not readable by the tool used here) | not supported as documented |
| Native ChatGPT Voice | "Currently available only in the ChatGPT desktop app, not on web or Android", for Plus, Pro, Business, Edu and Enterprise, in Chat, Work and Codex; it "follows the same permissions as the tasks it directs" | compatible under conditions (desktop app, macOS or Windows; rollout and workspace) |
| A custom MCP tool invoked from native Voice | Not stated anywhere in the material reviewed; OpenAI Support (2026-09-14, community forum) said "Desktop Voice in Work/Codex can use supported connected tools", without saying whether custom MCP servers count; community reports (May to September 2026) found connected tools unavailable in voice on the web and Android | **indeterminate until demonstrated** |
| A local stdio MCP server without public inbound exposure | Supported through Secure MCP Tunnel | compatible under conditions |

Consequences for the owner's equipment: the Linux desktop has no ChatGPT desktop app, so it can run test A (text, ChatGPT on the web) but not test C (native Voice); test C needs a macOS or Windows machine with the ChatGPT desktop app. The Android app is not a documented surface for custom MCP tools; what it offers can be recorded, but it cannot demonstrate native Voice + MCP as documented today.

## The diagnostic-only server

`python -m control_tv.mcp_diagnostic` (optional extra `control-tv[mcp]`, installed by `python3 scripts/dev.py setup`) is a separate stdio MCP server, `control-tv-diagnostic`. It fails closed and publishes exactly one tool:

- `tools/list` contains only `control_tv_diagnostic`; no discovery, status or TV command tool exists in this server (the control server, `control_tv.mcp_server`, is not used by S0). Any other tool name, and any argument, is refused (`invalid_argument`) without echoing it;
- `control_tv_diagnostic` takes no argument and answers only `server`, `version`, `ready`, `call` (per-process call number), `serverTimeUtc` and a fixed `scope` ("diagnostic only: no TV contacted, no discovery, no command"), with `tvContacted: false`: no device, media, network, personal, secret or credential data;
- it needs no TV and no discovery, and works with no TV connected: it imports neither the Cast layer, nor the bridge, nor the control service, and opens no socket (tests check that no `pychromecast`, `zeroconf`, bridge, service, adapters, embedded or control MCP server module is loaded);
- it is annotated read-only, idempotent, not destructive and closed-world, so ChatGPT should not ask for a write confirmation (to be recorded per surface);
- its log (stderr, and `--log-file PATH`, created with mode 0600 and only appended to) holds one line per call, `control_tv_diagnostic call #N at <UTC time>: ok` or `control_tv_diagnostic refused: invalid_argument`, plus the MCP SDK's own lines naming the request type only (`Processing request of type ListToolsRequest`, which shows a client reached the server even when it calls no tool). **Only a matching log line proves a call**: ChatGPT may describe a call it did not make;
- `--self-check` lists and calls the tool once through an in-memory MCP client, prints the result and exits 0 only if `tools/list` is exactly the one read-only tool and the call succeeded (no network, no ChatGPT).

## Connection: Secure MCP Tunnel, nothing public

Preferred route (Codex's audit): **Secure MCP Tunnel to the diagnostic-only stdio server** (`github.com/openai/tunnel-client`, Apache-2.0; Linux amd64 and arm64 builds; v0.0.16 of 2026-10-06).

- `tunnel-client` keeps an outbound-only HTTPS connection to OpenAI, starts the diagnostic server as its stdio child (`--mcp-command`) and relays MCP requests to it. The diagnostic server has no network port; no router port forwarding and no public endpoint exist.
- Authentication: a tunnel in the owner's OpenAI Platform organization, and a runtime API key restricted to **Tunnels Read + Use** (given to `tunnel-client` through `CONTROL_PLANE_API_KEY`, never an admin key, never on a shared command line, never in this repository, a commit, an issue or a shared log). Tunnel access is organization-level and granted by an organization owner or RBAC administrator.
- **Local listener of the tunnel client:** `tunnel-client` itself always runs a health/admin server (`/healthz`, `/readyz`, `/metrics`, an embedded UI with log export), by default on `127.0.0.1:8080`, without authentication; its UI and log endpoints answer loopback clients only unless `--allow-remote-ui` is set. It cannot be disabled. For S0: bind it to a Unix socket in a private directory (`--health.unix-socket <private-dir>/tunnel-health.sock`, directory mode 0700) or keep it on loopback, and **never** pass `--allow-remote-ui` or a non-loopback `--health.listen-addr`. So nothing is reachable from the network, but a local, unauthenticated, loopback-or-socket health surface does exist while the test runs.
- What transits OpenAI: the MCP requests and answers (tool list, the diagnostic call and its fixed answer). The diagnostic answer carries no device, network or personal data. `tunnel-client`'s log export redacts API keys, bearer tokens and URL secrets; its logs are still kept private.
- A public HTTPS endpoint (developer mode's other route) is not used: it would need TLS, authenticated and authorized access (OAuth), a strict tool allowlist, rate limiting, audit logging and secret rotation, and is out of scope for S0. A home-network MCP port is never exposed.

## Test and evidence protocol

Record every step in a private folder (for example `.ai-private/s0/`, git-ignored), redacting device identifiers, network data, user content, API keys, tunnel credentials and personal audio. Nothing private is committed.

### 1. Local MCP (no network, no ChatGPT)

```bash
python3 scripts/dev.py setup
.venv/bin/python -m control_tv.mcp_diagnostic --self-check
.venv/bin/python -m pytest -q tests/mcp_server/test_mcp_diagnostic.py
```

Pass: exit code 0; `"tools": ["control_tv_diagnostic"]`, `"readOnly": [true]`, `"ok": true`. Optionally confirm the same `tools/list` with the MCP Inspector against `.venv/bin/python -m control_tv.mcp_diagnostic`. Keep the TV switched off or unplugged during the whole S0 test: the diagnostic needs none.

### Preflight

Record: ChatGPT client and version, OS, plan, workspace or organization class (no identifiers), Voice availability, custom MCP / developer mode availability, relevant admin settings. Then start the tunnel (commands from `tunnel-client`'s own guide; `<TUNNEL_ID>` and the key come from the Platform and are not recorded):

```bash
tunnel-client init --sample sample_mcp_stdio_local --profile control-tv-diagnostic \
  --tunnel-id <TUNNEL_ID> \
  --mcp-command "<checkout>/.venv/bin/python -m control_tv.mcp_diagnostic --log-file <private-dir>/control-tv-diagnostic.log"
tunnel-client doctor --profile control-tv-diagnostic --explain
tunnel-client run --profile control-tv-diagnostic --health.unix-socket <private-dir>/tunnel-health.sock
```

Record the tunnel's health (`doctor` result, ready or not) without any secret. Follow the call log with `tail -f <private-dir>/control-tv-diagnostic.log`. In ChatGPT on the web: Plugins (or Settings -> Connectors, depending on the version) -> **Add custom MCP server** -> Connection: **Tunnel** -> select the tunnel (or paste its id); name it "control-TV diagnostic". If a flag above is refused by the installed `tunnel-client` version, record the refusal and keep its default loopback health server.

### A. ChatGPT text (web)

In a new text chat with the "control-TV diagnostic" server selected, type: *« Use the control-TV diagnostic tool once and report its structured result. »* (or its French equivalent). Capture the visible transcript, the tool shown as selected, its arguments (none), its result, any confirmation prompt, the time, the client and version, and the matching sanitized log line. Check exactly one invocation (one `call #N` line) and no retry.

### B. Diagnostic acceptance

Pass only if: the result is structured and stable (the fields above, `tvContacted: false`); `tools/list` as seen by ChatGPT contains only `control_tv_diagnostic`; no device, discovery or status data appears; no TV command or Cast session is created (the TV stays off).

### C. Native ChatGPT Voice (desktop app, macOS or Windows)

In the same account and workspace, in the ChatGPT desktop app, with the same server available, start a **native Voice conversation** (not voice dictation into the text box, which is not Voice evidence) and say the equivalent of A: *« Utilise l'outil de diagnostic control-TV une fois et donne-moi son résultat. »* Capture screen evidence showing the Voice surface, any tool confirmation, the final answer, the time, and the matching single server log line. Repeat in Work or Codex only if available and recorded as such. On the Linux desktop and on Android, record only what the app offers (Voice absent, server absent, or an attempt without a log line); this does not demonstrate native Voice + MCP.

### D. Evidence and failures

Keep sanitized screenshots and transcripts, client and account-class metadata, the plugin and tunnel state, the `tools/list` seen, exact error texts and codes, timestamps, and the server log lines (tool name, outcome, call number and time only). The absence of a server-side `call #N` line is evidence that the tool was not reached, not proof of why.

## GO / NO-GO

- **GO, compatible and demonstrated:** text (A) and native Voice (C) each produce exactly one matching server-side call to the diagnostic-only tool, return the expected structured result, expose no command tool and generate no TV traffic.
- **CONDITIONAL GO:** it succeeds only on a documented client, plan, workspace setting, rollout, plugin selection or approval flow; those conditions become deployment prerequisites (for example: the macOS or Windows desktop app).
- **NO-GO for the tested interface:** A succeeds, while controlled Voice attempts in an otherwise working Voice session produce no tool call or an explicit unsupported answer. This says nothing about other accounts or future rollouts.
- **INDETERMINATE:** access unavailable, setup incomplete, logs missing, rollout or workspace policy prevents the test, or only documentation and inference exist (the state today).

After the result: a GO or CONDITIONAL GO leads to P4 (a durable transport for the control tools, with its own security review and the owner's decision) and P3 (content on the TV from tools), each TV command only with the owner's go-ahead; a NO-GO on every surface the owner can use needs an owner decision before substantial P3 work (wait for Voice parity, use the desktop app on a macOS or Windows machine, or use ChatGPT text meanwhile).

## Effort (Codex's estimate, 2026-10-08)

Assuming the account already has the required features: diagnostic-only server, fail-closed tool surface, tests and documentation 0.5-1.5 person-days (this PR); tunnel and account setup with the text test 0.5-1.5 person-days; Voice test matrix, evidence and report 0.5-1 person-day; **S0 in total 1.5-4 person-days of focused effort**. Calendar time: 1-5 business days if permissions and rollout are already available, unbounded if the tested account or workspace does not offer custom MCP servers, tunnels or MCP tools in Voice (engineering cannot remove a product-side availability constraint). A durable production connection beyond S0 (lifecycle, credentials, packaging and startup, observability, possibly authenticated public hosting) is a separate 1-3 person-week effort.

## Sources (checked 2026-10-08)

Official:
- Custom MCP servers in ChatGPT: <https://developers.openai.com/api/docs/guides/custom-mcp-server>
- Secure MCP tunnels: <https://developers.openai.com/api/docs/guides/secure-mcp-tunnels>
- Connect an MCP server to ChatGPT: <https://developers.openai.com/plugins/deploy/connect-chatgpt>
- ChatGPT Voice: <https://learn.chatgpt.com/docs/features/voice>
- Plugins in ChatGPT: <https://learn.chatgpt.com/docs/plugins>
- Developer mode and MCP apps (help article; not readable by the tool used here, cited from Codex's review): <https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt>
- `tunnel-client`: <https://github.com/openai/tunnel-client>, its end-user guide <https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md> and configuration reference <https://github.com/openai/tunnel-client/blob/master/docs/configuration.md>
- ChatGPT Voice in the desktop app (OpenAI announcement, 2026-07-23): <https://community.openai.com/t/chatgpt-voice-is-now-in-the-desktop-app/1388031>

Community (reports, not official statements):
- Voice without the text-mode connected tools, with the OpenAI Support reply of 2026-09-14: <https://community.openai.com/t/give-voice-mode-access-to-the-same-connected-tools-as-text-mode/1393277>
- Apps and tools not triggered from voice mode (2026-07-03): <https://community.openai.com/t/voice-mode-support-for-chatgpt-apps-widgets-roadmap-and-best-practice-guidance/1385649>
- MCP in voice mode on web and Android (2026-05-31): <https://community.openai.com/t/chatgpt-support-of-mcp-in-voice-mode-on-web-and-android/1382072>
