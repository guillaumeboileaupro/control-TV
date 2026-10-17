import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { appLogo, catalogedPackages, resolveAppLogo, type AppLogoCatalogue } from "./app-logos.ts";

// Exact Android package -> the mark expected for it. A package missing here has no mark.
const MARKS = {
  "com.apple.atve.androidtv.appletv": "Apple TV",
  "com.android.vending": "Google Play",
  "com.google.android.apps.tv.launcherx": "Google TV",
  "com.google.android.youtube.tv": "YouTube",
  "com.google.android.youtube.tvkids": "YouTube Kids",
  "com.google.android.youtube.tvmusic": "YouTube Music",
  "com.mubi": "MUBI",
  "com.netflix.ninja": "Netflix",
  "com.plexapp.android": "Plex",
  "com.spotify.tv.android": "Spotify",
  "org.videolan.vlc": "VLC media player",
} as const;

const FILES = {
  "com.amazon.amazonvideo.livingroom": ["Prime Video", "/app-logos/prime-video.svg", "light"],
  "com.canal.android.canal": ["CANAL+", "/app-logos/canal-plus.svg", "light"],
  "com.disney.disneyplus": ["Disney+", "/app-logos/disney-plus.svg", "light"],
  "com.google.android.play.games": [
    "Google Play Games",
    "/app-logos/google-play-games.svg",
    "light",
  ],
  "com.molotov.app": ["Molotov", "/app-logos/molotov.svg", "dark"],
  "fr.francetv.pluzz": ["france.tv", "/app-logos/france-tv.svg", "light"],
  "fr.tf1.mytf1": ["TF1+", "/app-logos/tf1-plus.svg", "light"],
  "tv.arte.plus7": ["ARTE", "/app-logos/arte.svg", "light"],
} as const;

describe("TV app logos", () => {
  test("maps exact Android packages to their bundled Simple Icons path", () => {
    for (const [packageName, title] of Object.entries(MARKS)) {
      const logo = appLogo(packageName);
      assert.ok(logo, packageName);
      assert.equal(logo.kind, "mark", packageName);
      assert.equal(logo.title, title);
      if (logo.kind === "mark") {
        assert.match(logo.path, /^[Mm]/);
        assert.match(logo.hex, /^[0-9A-F]{6}$/);
      }
    }
  });

  test("maps exact Android packages to their shipped official logo file", () => {
    for (const [packageName, [title, src, background]] of Object.entries(FILES)) {
      assert.deepEqual(appLogo(packageName), { kind: "image", title, src, background });
    }
  });

  test("the catalogue holds exactly the expected packages, nothing more", () => {
    const expected = [...Object.keys(MARKS), ...Object.keys(FILES)].sort();
    assert.deepEqual([...catalogedPackages()].sort(), expected);
  });

  test("never guesses a logo for an unknown, excluded or similar package", () => {
    for (const packageName of [
      "com.example.netflix",
      "com.netflix.ninja.fake",
      "Netflix",
      "ARTE",
      "tv.arte.plus7.beta",
      // M6+ has no verified logo of its own: the M6 channel mark would be an approximation.
      "fr.m6.m6replay.by",
      "com.android.tv.settings",
      "com.google.android.tvrecommendations",
      // Keys inherited from Object.prototype are never catalogue entries.
      "constructor",
      "__proto__",
      "toString",
    ]) {
      assert.equal(appLogo(packageName), null, packageName);
    }
  });

  test("rejects entries whose path, colour or file is not valid and local", () => {
    const catalogue: AppLogoCatalogue = {
      "com.example.empty": { kind: "mark", title: "Empty", path: "  ", hex: "123456" },
      "com.example.colour": { kind: "mark", title: "Colour", path: "M0 0", hex: "" },
      "com.example.remote": {
        kind: "image",
        title: "Remote",
        src: "https://cdn.example/logo.svg",
        background: "light",
      },
      "com.example.escape": {
        kind: "image",
        title: "Escape",
        src: "/app-logos/../logo.svg",
        background: "light",
      },
      "com.example.mark": { kind: "mark", title: "Mark", path: "M0 0", hex: "123ABC" },
      "com.example.file": {
        kind: "image",
        title: "File",
        src: "/app-logos/file.svg",
        background: "dark",
      },
    };
    for (const name of ["empty", "colour", "remote", "escape"]) {
      assert.equal(resolveAppLogo(`com.example.${name}`, catalogue), null, name);
    }
    assert.deepEqual(resolveAppLogo("com.example.mark", catalogue), catalogue["com.example.mark"]);
    assert.deepEqual(resolveAppLogo("com.example.file", catalogue), catalogue["com.example.file"]);
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
      assert.ok(appLogo("tv.arte.plus7"));
      assert.equal(appLogo("com.example.unknown"), null);
      assert.equal(requests, 0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
