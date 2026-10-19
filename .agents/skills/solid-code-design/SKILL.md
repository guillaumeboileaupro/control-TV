---
name: solid-code-design
description: Design, implement, and refactor maintainable code using SOLID principles and explicit architecture boundaries. Use for feature work, structural refactoring, dependency inversion, oversized modules or classes, unstable interfaces, duplicated responsibilities, and design reviews. Do not activate for prose-only edits or purely mechanical formatting.
---

# SOLID Code Design

Apply SOLID as a decision framework, not as a reason to add layers. Preserve observable behavior and prefer the smallest cohesive design that improves changeability or testability.

## Start from responsibilities

- Read repository architecture, ownership boundaries, tests, and public interfaces before changing structure.
- Identify the concrete change pressure or defect. Do not introduce abstractions without a current consumer, boundary, or independently varying behavior.
- Keep domain rules independent from UI, transport, persistence, frameworks, devices, and third-party libraries.
- Keep adapters thin: translate external data, validate it, invoke the domain, and translate the result.

## Apply SOLID deliberately

- **Single responsibility:** give a module, class, or function one reason to change. Split orchestration, validation, serialization, I/O, and domain decisions when they vary independently; keep tightly coupled behavior together.
- **Open/closed:** add behavior through an existing stable seam when one exists. Prefer a focused strategy, protocol, or data-driven mapping over expanding conditionals, but do not create a plugin system for a single case.
- **Liskov substitution:** every implementation must honor the interface's inputs, outputs, errors, timing, and side-effect guarantees. Tests for a port apply to every adapter. Do not strengthen preconditions or invent success.
- **Interface segregation:** expose the smallest capability a consumer needs. Avoid broad service objects and protocols whose implementations require irrelevant methods.
- **Dependency inversion:** domain and application policy depend on project-owned ports or callables; infrastructure implements them. Instantiate concrete dependencies only at composition roots.

## Type and boundary discipline

- Production code must not use `Any` merely to bypass design or typing. Prefer domain types, `Protocol`, generics, typed records, and validated JSON shapes. A test may use `Any` for deliberately malformed or dynamic fixtures when that keeps the behavior under test clear.
- Use TypeScript `unknown` only for genuinely untrusted inputs. Narrow it with runtime checks before domain use; do not replace it with an assertion that assumes the answer.
- Contain untyped third-party APIs in one adapter and expose a project-owned typed interface.
- Preserve the distinction between a requested action, a sent action, a reported state, and a physically observed outcome when the domain requires it.

## Refactoring discipline

1. Characterize current behavior with existing or focused regression tests.
2. Name the responsibility or dependency being separated and the concrete benefit.
3. Refactor in small steps without changing unrelated interfaces.
4. Remove superseded branches and abstractions; do not leave parallel implementations.
5. Run strict type checking, lint, formatting, and relevant tests. Verify architecture direction and search for newly introduced bypasses or cycles.

## Review questions

- Can this unit be described without “and” joining unrelated responsibilities?
- Can a replacement implementation preserve the same contract without knowing its consumers?
- Does each consumer depend only on operations it uses?
- Do domain decisions import infrastructure or framework details?
- Is the abstraction cheaper to understand than the duplication or conditional it replaces?
- Are failures and side effects explicit, bounded, and testable?

## Report

State which responsibility or dependency changed, the interface and behavior preserved, why the abstraction is justified now, checks run, and remaining design debt. Mention principles only where they explain a concrete decision.
