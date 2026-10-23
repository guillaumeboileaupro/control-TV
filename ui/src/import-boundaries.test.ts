import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { basename, dirname, extname, resolve } from "node:path";
import { describe, test } from "node:test";
import ts from "typescript";

const SOURCE_ROOT = resolve(process.cwd(), "src");

function moduleReferences(text: string, name = "fixture.ts"): string[] {
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
  const references: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      references.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length === 1 &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      references.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return references;
}

function productionModules(): Map<string, string[]> {
  const names = readdirSync(SOURCE_ROOT)
    .filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"))
    .sort();
  const known = new Set(names);
  const graph = new Map<string, string[]>();
  for (const name of names) {
    const path = resolve(SOURCE_ROOT, name);
    const dependencies = moduleReferences(readFileSync(path, "utf8"), name)
      .filter((reference) => reference.startsWith("."))
      .map((reference) => {
        const candidate = resolve(dirname(path), reference);
        const withExtension = extname(candidate) ? candidate : `${candidate}.ts`;
        return basename(withExtension);
      })
      .filter((dependency) => known.has(dependency));
    graph.set(name, [...new Set(dependencies)].sort());
  }
  return graph;
}

function cycles(graph: ReadonlyMap<string, readonly string[]>): string[][] {
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];
  const found: string[][] = [];
  const walk = (name: string): void => {
    if (visiting.has(name)) {
      const start = stack.indexOf(name);
      found.push([...stack.slice(start), name]);
      return;
    }
    if (visited.has(name)) return;
    visiting.add(name);
    stack.push(name);
    for (const dependency of graph.get(name) ?? []) walk(dependency);
    stack.pop();
    visiting.delete(name);
    visited.add(name);
  };
  for (const name of graph.keys()) walk(name);
  return found;
}

describe("frontend import boundaries", () => {
  test("the complete production module graph is acyclic", () => {
    assert.deepEqual(cycles(productionModules()), []);
  });

  test("the root model does not depend on feature reducers and shared state stays a leaf", () => {
    const graph = productionModules();
    assert.deepEqual(graph.get("view-state.ts"), []);
    assert.equal(graph.get("model.ts")?.includes("playback.ts"), false);
    assert.equal(graph.get("model.ts")?.includes("remote.ts"), false);
    assert.equal(graph.get("model.ts")?.includes("view-state.ts"), true);
  });

  test("the parser covers static, side-effect and dynamic forms", () => {
    assert.deepEqual(
      moduleReferences(`
        import value from "./static";
        export { value } from "./reexport.ts";
        import "./effect";
        const lazy = import("./dynamic.ts");
      `),
      ["./static", "./reexport.ts", "./effect", "./dynamic.ts"],
    );
  });

  test("an indirect cycle is rejected", () => {
    const graph = new Map([
      ["model.ts", ["shared.ts"]],
      ["shared.ts", ["feature.ts"]],
      ["feature.ts", ["model.ts"]],
    ]);
    assert.deepEqual(cycles(graph), [["model.ts", "shared.ts", "feature.ts", "model.ts"]]);
  });
});
