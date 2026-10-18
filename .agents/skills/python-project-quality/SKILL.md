---
name: python-project-quality
description: Structure, test, type-check, and review maintainable Python applications. Use for Python repositories with domain logic, FastAPI or Flask APIs, SQLite persistence, desktop launchers, pytest, mypy, packaging, or CI quality gates.
---

# Python Project Quality

Keep domain behavior testable, interfaces thin, and verification reproducible in the repository's own environment.

## Inspect before changing

- Read repository instructions, dependency files, test configuration, CI workflows, and the current module boundaries.
- Use the project's virtual environment and invoke tools through its Python interpreter when possible.
- If global pytest plugins interfere, diagnose the environment before changing project code. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is a diagnostic option, not a default project setting.

## Architecture

- Keep domain rules pure and independent from the web framework, desktop shell, and database.
- Treat the backend as the source of truth for validation and game or business state. Do not duplicate authoritative rules in JavaScript.
- Separate domain logic, API transport, persistence, types, and platform launchers.
- Use explicit typed structures at boundaries and avoid `Any` unless the external interface genuinely requires it.
- Keep SQLite paths configurable for tests and store user data outside the source tree in production.

## Verification

- Add focused unit tests for domain rules, edge cases, invalid input, and state transitions.
- Add API tests for request validation, response shape, status codes, persistence, and complete user flows.
- Use isolated temporary databases and deterministic fixtures.
- Run the repository's own commands for tests, strict type checking, linting, formatting, and packaging.
- Preserve or raise the existing coverage threshold. Do not weaken tests or exclusions merely to make CI green.
- Review warnings and skipped tests. Report them rather than hiding them.

## Change discipline

- Reproduce a defect before fixing it when practical, then add a regression test.
- Prefer the smallest coherent change that preserves current behavior outside the requested scope.
- Update the README and TODO when commands, architecture, limitations, or completed work change.
- Document how to start, stop, test, build, install, and uninstall the application using commands verified in the repository.

## Report

State which checks ran, their results, coverage when available, files or behaviors not exercised, and any environment limitation.
