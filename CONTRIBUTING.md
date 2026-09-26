# Contributing to control-TV

Thank you for your interest in contributing to control-TV.

control-TV is under active development. Contributions are welcome, but changes should remain focused, tested, and consistent with the project's architecture and development plan.

## Before Contributing

Before starting significant work:

1. Read `README.md`, `ARCHITECTURE.md`, and `DEVELOPMENT_PLAN.md`.
2. Check existing issues and pull requests to avoid duplicating work.
3. Keep changes focused on a single concern whenever possible.
4. For substantial features or architectural changes, open an issue or discussion before implementation.

Do not mix unrelated refactoring, formatting, documentation, UI, and functional changes in the same pull request.

## Development Areas

The project currently contains several distinct areas:

- Python domain and application logic
- PyChromecast integration
- Python bridge
- Rust/Tauri desktop shell
- TypeScript frontend
- tests, tooling, CI, and documentation

Keep responsibilities separated according to the existing architecture. In particular, Cast-specific business logic must not be duplicated in the Rust/Tauri or frontend layers.

## Development Environment

Use the repository's existing development tools and lockfiles.

Do not commit generated dependencies, build outputs, caches, virtual environments, local screenshots, or temporary files.

The repository provides `scripts/dev.py` for common development and validation tasks. Prerequisites and the full command list are in `README.md` (Development).

Before running any check:

```bash
python3 scripts/dev.py setup     # Python environment from uv.lock (requires uv)
npm install --prefix ui          # frontend and Tauri CLI dependencies (for ui-check, rust-check and the app)
```

Run the application from the repository root with `npx --prefix ui tauri dev`. Builds leave large output in `src-tauri/target`; check it with `python3 scripts/dev.py disk-usage` and remove it with `python3 scripts/dev.py clean`.

## Quality Requirements

Before submitting a pull request, run the checks relevant to your changes.

For the Python codebase:

```bash
python3 scripts/dev.py check
python3 scripts/dev.py coverage
```

Python changes are expected to pass:

- Ruff linting
- Ruff formatting checks
- mypy in strict mode
- pytest
- the project's coverage requirement
- dependency compatibility checks

For Rust/Tauri changes:

```bash
python3 scripts/dev.py rust-check
```

Rust changes must pass formatting, Clippy with warnings treated as errors, and the Rust test suite.

For frontend changes:

```bash
python3 scripts/dev.py ui-check
```

TypeScript and UI changes must pass the configured type checking, formatting, and frontend tests.

Do not weaken tests, type checking, linting, coverage requirements, or CI gates merely to make a change pass.

## Tests

Every behavioral change should have appropriate automated tests.

Regression fixes should include a test that would fail without the fix whenever practical.

Tests should be deterministic and should not depend on a real Chromecast, Google TV, local network configuration, or other developer-specific environment unless they are explicitly designated as hardware validation.

Automated tests and physical-device validation are separate forms of evidence. Do not claim hardware validation based only on mocks, fakes, or automated tests.

## Physical Device Validation

Some features require validation against real Google Cast hardware.

Physical validation must be reported explicitly as either performed or not performed.

Never send commands to a real device merely to satisfy an automated test.

Follow `docs/CAST_HARDWARE_VALIDATION.md` and keep any device-identifying record local.

When performing hardware validation:

- use only devices you are authorized to control
- send commands deliberately and one at a time when practical
- distinguish observed device behavior from inferred behavior
- do not claim success when the result is ambiguous
- document which operations were actually tested

## Privacy and Local Network Data

control-TV discovers and communicates with devices on the local network.

Never commit or publish:

- private IP addresses
- device UUIDs or other unique device identifiers
- personal device names when they reveal private information
- credentials or authentication material
- tokens
- private network topology
- screenshots containing sensitive local-network information

Use sanitized or synthetic values in tests, documentation, issues, and pull requests.

If a screenshot containing real device or network information is needed for local validation, inspect it locally and delete it when it is no longer required.

## User Interface Contributions

UI changes should preserve the project's desktop-first, minimal interface and remain usable at smaller window sizes.

Before making significant UI changes, review the project's current design guidance and relevant UI skills or documentation when available.

Prefer:

- clear visual hierarchy
- restrained use of color
- consistent spacing
- accessible controls
- explicit loading, error, disconnected, partial, and unavailable states
- keyboard-accessible interactions
- reuse of the project's validated visual assets

Avoid unnecessary visual complexity, duplicated information, decorative effects that do not improve usability, and generic dashboard-style layouts.

UI changes must not duplicate backend or Cast business logic.

## Commit Messages

Use concise Conventional Commit-style messages where practical, for example:

```text
feat(ui): add playback controls
fix(cast): preserve discovery lifecycle
test(control): cover playback confirmation
docs: update security policy
```

Keep commits understandable and focused.

## Pull Requests

Pull requests should:

- explain what changed and why
- identify the affected components
- include relevant automated validation results
- state whether physical-device validation was performed
- describe any known limitations or follow-up work
- avoid unrelated changes
- contain no private network or device information

Do not describe a check as passing unless it was actually run successfully on the submitted code.

All required CI checks should pass before merge.

Review feedback should be addressed or explicitly discussed before merging.

## Security Issues

Do not report security vulnerabilities through public issues, discussions, or pull requests.

Follow the process described in `SECURITY.md`.

## License

By contributing to control-TV, you agree that your contributions will be licensed under the same license as the project.

control-TV is distributed under the GNU General Public License version 3. See `LICENSE` for the full license text.
