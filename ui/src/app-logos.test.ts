import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { appLogo } from "./app-logos.ts";

describe("TV app logos", () => {
  test("maps an exact Android package to its bundled logo", () => {
    const logo = appLogo("com.netflix.ninja");
    assert.equal(logo?.title, "Netflix");
    assert.match(logo?.path ?? "", /^[Mm]/);
    assert.match(logo?.hex ?? "", /^[0-9A-F]{6}$/);
  });

  test("never guesses a logo for an unknown or similar package", () => {
    assert.equal(appLogo("com.example.netflix"), null);
    assert.equal(appLogo("com.netflix.ninja.fake"), null);
  });
});
