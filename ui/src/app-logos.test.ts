import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { appLogo, resolveAppLogo, type AppLogoCatalogue } from "./app-logos.ts";

describe("TV app logos", () => {
  test("maps exact Android packages to valid bundled SVG paths", () => {
    const packages = {
      "com.apple.atve.androidtv.appletv": "Apple TV",
      "com.android.tv.settings": "Android",
      "com.android.vending": "Google Play",
      "com.google.android.apps.tv.launcherx": "Google TV",
      "com.google.android.play.games": "Google Play",
      "com.google.android.tvrecommendations": "Google TV",
      "com.google.android.youtube.tv": "YouTube",
      "com.google.android.youtube.tvkids": "YouTube Kids",
      "com.google.android.youtube.tvmusic": "YouTube Music",
      "com.mubi": "MUBI",
      "com.netflix.ninja": "Netflix",
      "com.plexapp.android": "Plex",
      "com.spotify.tv.android": "Spotify",
      "org.videolan.vlc": "VLC media player",
    } as const;
    for (const [packageName, title] of Object.entries(packages)) {
      const logo = appLogo(packageName);
      assert.ok(logo, packageName);
      assert.equal(logo.title, title);
      assert.match(logo.path, /^[Mm]/);
      assert.match(logo.hex, /^[0-9A-F]{6}$/);
    }
  });

  test("never guesses a logo for an unknown or similar package", () => {
    assert.equal(appLogo("com.example.netflix"), null);
    assert.equal(appLogo("com.netflix.ninja.fake"), null);
    assert.equal(appLogo("Netflix"), null);
  });

  test("rejects catalogue entries whose SVG path or colour is missing", () => {
    const catalogue: AppLogoCatalogue = {
      "com.example.empty": { title: "Empty", path: "  ", hex: "123456" },
      "com.example.colour": { title: "Colour", path: "M0 0", hex: "" },
      "com.example.valid": { title: "Valid", path: "M0 0", hex: "123ABC" },
    };
    assert.equal(resolveAppLogo("com.example.empty", catalogue), null);
    assert.equal(resolveAppLogo("com.example.colour", catalogue), null);
    assert.deepEqual(
      resolveAppLogo("com.example.valid", catalogue),
      catalogue["com.example.valid"],
    );
  });

  test("resolves known and unknown packages without a network request", () => {
    const originalFetch = globalThis.fetch;
    let requests = 0;
    globalThis.fetch = () => {
      requests += 1;
      throw new Error("logo lookup must remain local");
    };
    try {
      assert.ok(appLogo("com.netflix.ninja"));
      assert.equal(appLogo("com.example.unknown"), null);
      assert.equal(requests, 0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
