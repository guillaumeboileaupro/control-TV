---
name: typescript-no-any
description: Eliminate and prevent explicit or implicit `any` in TypeScript code. Use when writing, changing, reviewing, or hardening TypeScript types, boundaries, callbacks, generics, tests, and configuration. Do not activate for prose-only edits or repositories without TypeScript.
---

# TypeScript Without Any

Produce TypeScript in which application-owned code contains no explicit or implicit `any`, while preserving behavior and the repository's existing public interfaces.

## Inspect the project first

- Read the repository instructions and the relevant TypeScript, compiler, lint, test, and package configuration.
- Use the project's existing commands and dependency manager. Do not add a linter, plugin, or dependency unless the task requires persistent enforcement and the repository does not already provide it.
- Search code, declarations, suppressions, and configuration for `any`, disabled no-`any` rules, and weakened compiler options. Do not treat the English word “any” in comments, strings, or test names as a type violation.

## Type rules

- Do not introduce explicit `any`, including `any[]`, `Array<any>`, `Record<string, any>`, generic defaults or constraints using `any`, casts such as `as any`, and `(...args: any[])`.
- Do not silence an implicit `any` with a broad assertion, `@ts-ignore`, disabled lint rule, or weaker compiler setting.
- Keep `strict` and `noImplicitAny` enabled when they are already enabled. Never weaken type checking to make validation pass.
- Prefer the narrowest accurate type. Use named domain types for stable concepts, generics when relationships between types matter, and `unknown` only at genuinely untrusted or opaque boundaries.
- Narrow `unknown` before use with runtime checks, discriminated unions, schema validation already present in the project, or focused type guards. A cast without evidence is not validation.
- For JSON, IPC, network, storage, browser globals, third-party libraries, and test doubles, type the boundary explicitly and validate external data before converting it to a domain type.
- Preserve intentional API compatibility. If an upstream declaration exposes `any`, contain it at one adapter boundary and expose a safe typed interface to application code. Do not copy the unsafe type through the codebase.
- Avoid replacing `any` with misleading escape hatches such as `object`, `{}`, `Function`, unchecked index signatures, or an overly broad union. `never` is appropriate only for impossible states or exhaustive checks.
- In tests, prefer typed fixtures and small fakes, but `any` is permitted when a fixture deliberately represents malformed, untyped, or highly dynamic external data. Keep it local to the test and never let it justify `any` in production code.

## Change workflow

1. Locate real type-level occurrences and identify why each exists: missing annotation, unsafe boundary, weak third-party declaration, generic design, or test fixture.
2. Trace callers and consumers before choosing a replacement so the new type models actual data flow rather than merely satisfying the compiler.
3. Make the smallest coherent correction. Add runtime validation when data is untrusted; otherwise express the existing invariant statically.
4. Add or update focused tests when narrowing or boundary validation changes observable handling of invalid data.
5. Run the repository's typecheck, lint or formatting checks, and relevant tests. Search again for forbidden type-level uses and inspect suppressions/configuration changes.

## Persistent enforcement

When the user asks to prevent future regressions, prefer both compiler and lint enforcement where the repository supports them:

- TypeScript `strict: true` (or at minimum `noImplicitAny: true`) catches implicit `any`.
- A type-aware lint rule such as `@typescript-eslint/no-explicit-any` catches explicit `any`.

Integrate enforcement into the existing quality command and CI rather than inventing a parallel workflow. Do not claim `tsc --noEmit` alone rejects every explicit `any`; it generally does not.

## Report

State the affected files or boundaries, the checks run and their exact results, any remaining upstream or generated `any` outside application-owned code, and any unvalidated area. Distinguish a clean search from compiler/linter proof.
