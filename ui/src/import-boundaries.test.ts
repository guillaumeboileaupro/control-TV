import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, test } from "node:test";

function source(name: string): string {
  return readFileSync(resolve(process.cwd(), "src", name), "utf8");
}

describe("frontend import boundaries", () => {
  test("the root model does not depend on feature reducers", () => {
    const model = source("model.ts");

    assert.doesNotMatch(model, /from ["']\.\/(?:playback|remote)\.ts["']/);
    assert.match(model, /from ["']\.\/view-state\.ts["']/);
  });

  test("shared state shapes remain a leaf module", () => {
    const shared = source("view-state.ts");

    assert.doesNotMatch(shared, /^\s*(?:import|export)\s+[^;]*\sfrom\s/m);
  });
});
