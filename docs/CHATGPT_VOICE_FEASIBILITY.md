# ChatGPT voice and control-TV tools: feasibility (gate G1, S0)

Question (issue #34, `DEVELOPMENT_PLAN.md` item O): can the **native voice mode of the ChatGPT app** call control-TV's custom MCP tools? This file gives the verdict from the documentation available on 2026-10-08, the read-only diagnostic tool that settles it empirically, and the exact test protocol. Nothing here sends a command to a TV.

## Verdict (2026-10-08, before the empirical test)

**Not established. On the evidence available, the owner's voice surfaces (the ChatGPT Android app and ChatGPT on the web, used from Linux) are unlikely to call custom MCP tools in voice mode today; text mode is documented and expected to work.** The empirical test below decides it; until it is run, nothing is promised.

| Surface | Custom MCP tools | Evidence | Status |
|---|---|---|---|
| ChatGPT text mode (web, desktop app, mobile) with developer mode | yes, documented: remote MCP servers (Streamable HTTP or SSE), OAuth or no authentication, or a Secure MCP Tunnel | OpenAI developer announcement (2025-09); `openai/tunnel-client` guide ("Connection: Tunnel") | expected to work; to be observed (test T1) |
| ChatGPT voice mode on Android and on the web | **reported not available**: connectors available in text mode were not usable after switching to voice | community reports of 2026-05-31, 2026-07-03 and 2026-08-28/09-08 (Outlook and Notion connectors unavailable in voice); no official statement of support | unlikely; to be observed (test T2) |
| ChatGPT Voice in the desktop app (macOS, Windows; no Linux app) | **partly**: OpenAI Support (2026-09-14) says "Desktop Voice in Work/Codex can use supported connected tools"; whether custom developer-mode or tunnel connectors count as "supported connected tools" is not stated | OpenAI announcement of 2026-07-23 (desktop voice, macOS and Windows, Plus/Pro/Business/Edu/Enterprise); OpenAI Support reply of 2026-09-14 | possible only on macOS or Windows; to be observed if such a machine is available (test T3) |

The owner's desktop is Linux, which has no ChatGPT desktop app, so the one officially mentioned voice-with-tools path (desktop voice in Work/Codex) needs a macOS or Windows machine.

## The diagnostic tool

`python -m control_tv.mcp_diagnostic [--log-file PATH]` (optional extra `control-tv[mcp]`, installed by `python3 scripts/dev.py setup`) is a separate stdio MCP server, `control-tv-diagnostic`, with exactly one tool:

- `control_tv_diagnostic` (optional `note`, a string of at most 200 characters): answers its per-process call number, the server's UTC time, the note and `tvContacted: false`;
- read-only by construction: it imports neither the Cast layer, nor the bridge, nor the control service, opens no socket and cannot reach a TV (tests: no `pychromecast`, `zeroconf`, bridge, service, adapters or control MCP server module is ever loaded); annotated read-only, idempotent, not destructive, closed world;
- evidence: each call is logged (stderr, and the `--log-file` file, created with mode 0600 and appended to) as `diagnostic call #N at <UTC time> (note: K characters)`; the note itself is never logged. **Only a log line proves a call**: ChatGPT may describe a call it did not make (a community report of 2026-08-28 shows voice mode claiming an action it had not done), so its answer alone is not evidence;
- it is not the control server: `control_tv.mcp_server` and its command tools are not exposed by this test.

## Connection without exposing anything

The diagnostic server is never given a network port. It is reached through **OpenAI's Secure MCP Tunnel** (`github.com/openai/tunnel-client`, Apache-2.0, Linux amd64/arm64 builds published, v0.0.16 of 2026-10-06): the tunnel client, run by the owner, keeps an outbound-only HTTPS connection to OpenAI, starts the diagnostic server as a stdio subprocess (`--mcp-command`) and relays the MCP requests to it. Authentication is the tunnel's: a tunnel id, and a runtime API key restricted to Tunnels Read and Use in the owner's OpenAI Platform organization. No inbound port, no public URL and no unauthenticated endpoint exists. What it implies:

- an OpenAI Platform organization and its keys are needed (for the test only; the control-TV application itself still calls no OpenAI API);
- the MCP requests and answers transit OpenAI (for the diagnostic: the tool name, the note and the call number, time and version);
- the tunnel client must stay running during the test;
- the keys never go into this repository, a commit, an issue or a log shared publicly.

A public HTTPS endpoint (developer mode's other option) is not used: it would need its own authentication (OAuth) and security review, and is out of scope for this test.

## Test protocol

Prerequisites, on the owner's side:
1. A ChatGPT plan whose settings offer developer mode (Settings -> Connectors -> Advanced -> Developer mode; the menu names can differ between ChatGPT versions). Which plans allow custom connectors, and with which limits (read-only on some individual plans, according to third-party guides), is not confirmed by an official source available here: check it on the owner's account and record it.
2. In the OpenAI Platform: a tunnel (Tunnels management) and a runtime API key with Tunnels Read + Use only.
3. On the Linux desktop: this branch checked out with `python3 scripts/dev.py setup` done; the tunnel client from its GitHub release (`tunnel-client-runtime-v0.0.16-linux-amd64.zip`), checked against the release's `SHA256SUMS.txt`.

Local check (no network, no ChatGPT):
```bash
.venv/bin/python -m pytest -q tests/mcp_server/test_mcp_diagnostic.py
```

Start the tunnel (the runtime key is given to `tunnel-client` as its own guide instructs, never on the command line of a shared shell history or in this repository):
```bash
tunnel-client init --sample sample_mcp_stdio_local --profile control-tv-diagnostic \
  --tunnel-id <TUNNEL_ID> \
  --mcp-command "<checkout>/.venv/bin/python -m control_tv.mcp_diagnostic --log-file <private-dir>/control-tv-diagnostic.log"
tunnel-client doctor --profile control-tv-diagnostic --explain
tunnel-client run --profile control-tv-diagnostic
```
Keep `<private-dir>` outside the repository (or under the git-ignored `.ai-private/`). Follow the log with `tail -f <private-dir>/control-tv-diagnostic.log`.

In ChatGPT: Settings -> Connectors (<https://chatgpt.com/#settings/Connectors>) -> create a connector named "control-TV diagnostic", Connection: Tunnel, select the tunnel. Then:

- **T1, text (web on Linux, then the Android app):** a new chat with the connector enabled for that chat; type: *« Utilise control-TV pour tester la connexion, avec la note "test texte 1". »* Success: a new log line (`diagnostic call #N`) at that time, and ChatGPT's answer quoting the same call number. Record whether ChatGPT asked for a confirmation before the call.
- **T2, voice (the Android app, then the web):** in a chat where the connector is enabled, switch to voice mode and say: *« Teste la connexion control-TV avec la note test vocal un. »* Success only if a new log line appears at that moment and the spoken answer gives its call number. No new line means voice mode did not call the tool, whatever ChatGPT says.
- **T3, desktop voice (only if a macOS or Windows machine with the ChatGPT desktop app is available):** the same request in desktop voice, in a normal chat, then in Work/Codex if the plan offers it.

Record for each attempt, without any key, account identifier or device data: date and time, surface (app and version, OS), plan, mode (text or voice), whether the connector was offered, whether a confirmation was asked, the log line (call number and time) or its absence, and ChatGPT's answer in one sentence. Stop the tunnel client afterwards; the connector can be deleted or left disabled.

## Decision after the test

- **Voice calls the tool on a surface the owner uses:** G1 is met for that surface; next, P4 (a transport for the control tools, with its own security review and the owner's decision) and P3 (content on the TV from tools), each command against a real TV only with the owner's go-ahead.
- **Voice does not call it on the owner's surfaces:** G1 fails there; the central path needs an owner decision before substantial P3 work: wait for voice parity (tracked, not promised), use desktop voice on a macOS or Windows machine if T3 succeeds (Codex there can also start the local stdio control server directly, which a real Codex client already reached read-only in PR #31), or use ChatGPT text mode meanwhile.
- **Text does not work either:** the plan or the tunnel setup is the blocker; record what failed before anything else.

## Sources (checked 2026-10-08)

- OpenAI tunnel client: <https://github.com/openai/tunnel-client> and its end-user guide <https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md> (releases: v0.0.16, 2026-10-06, Linux builds).
- OpenAI developers, full MCP tools in ChatGPT developer mode: <https://x.com/OpenAIDevs/status/1965807401745207708>.
- OpenAI help, developer mode and MCP apps: <https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt> (not readable by the tool used here: HTTP 403).
- ChatGPT Voice in the desktop app (OpenAI announcement, 2026-07-23): <https://community.openai.com/t/chatgpt-voice-is-now-in-the-desktop-app/1388031>; TechCrunch, 2026-07-24: <https://techcrunch.com/2026/07/24/openais-new-voice-mode-makes-it-to-the-chatgpt-desktop-app/>.
- Voice mode without the text-mode connectors, with the OpenAI Support reply of 2026-09-14: <https://community.openai.com/t/give-voice-mode-access-to-the-same-connected-tools-as-text-mode/1393277>.
- Apps and tools not triggered from voice mode (2026-07-03): <https://community.openai.com/t/voice-mode-support-for-chatgpt-apps-widgets-roadmap-and-best-practice-guidance/1385649>; MCP in voice mode on web and Android (2026-05-31): <https://community.openai.com/t/chatgpt-support-of-mcp-in-voice-mode-on-web-and-android/1382072>.
- Plan availability of custom connectors, third-party and unconfirmed: <https://peliqan.io/blog/chatgpt-mcp/>.
