// Central catalogue of locally bundled brand marks for exact Android package identifiers.
// Unknown or invalid entries deliberately resolve to null: callers retain their initial fallback.
// Two kinds of mark: a single Simple Icons path, or an official logo file shipped with the app
// under assets/app-logos/ (served at /app-logos/, never fetched from the network). Sources and
// licences: docs/APP_LOGOS.md.

import {
  siAppletv,
  siGoogleplay,
  siGoogletv,
  siHbomax,
  siMubi,
  siNetflix,
  siParamountplus,
  siPlex,
  siSpotify,
  siVlcmediaplayer,
  siYoutube,
  siYoutubekids,
  siYoutubemusic,
  type SimpleIcon,
} from "simple-icons";

// A one-colour Simple Icons path on a 24×24 grid.
export interface AppMark {
  readonly kind: "mark";
  readonly title: string;
  readonly path: string;
  readonly hex: string;
}

// An official logo file shipped with the app. `background` is the surface the brand's own
// colours are drawn for (Molotov's white letters need a dark one); the logo itself is unchanged.
export interface AppLogoImage {
  readonly kind: "image";
  readonly title: string;
  readonly src: string;
  readonly background: "light" | "dark";
}

export type AppLogo = AppMark | AppLogoImage;

export type AppLogoCatalogue = Readonly<Record<string, AppLogo | null>>;

function bundled(icon: SimpleIcon): AppMark {
  return { kind: "mark", title: icon.title, path: icon.path, hex: icon.hex };
}

function shipped(
  title: string,
  file: string,
  background: "light" | "dark" = "light",
): AppLogoImage {
  return { kind: "image", title, src: `/app-logos/${file}.svg`, background };
}

const LOGOS: AppLogoCatalogue = {
  "com.amazon.amazonvideo.livingroom": shipped("Prime Video", "prime-video"),
  "com.apple.atve.androidtv.appletv": bundled(siAppletv),
  "com.android.vending": bundled(siGoogleplay),
  "com.canal.android.canal": shipped("CANAL+", "canal-plus"),
  "com.cbs.ca": bundled(siParamountplus),
  "com.disney.disneyplus": shipped("Disney+", "disney-plus"),
  "com.google.android.apps.tv.launcherx": bundled(siGoogletv),
  "com.google.android.play.games": shipped("Google Play Games", "google-play-games"),
  "com.google.android.videos": bundled(siGoogletv),
  "com.google.android.youtube.tv": bundled(siYoutube),
  "com.google.android.youtube.tvkids": bundled(siYoutubekids),
  "com.google.android.youtube.tvmusic": bundled(siYoutubemusic),
  "com.molotov.app": shipped("Molotov", "molotov", "dark"),
  "com.mubi": bundled(siMubi),
  "com.netflix.ninja": bundled(siNetflix),
  "com.plexapp.android": bundled(siPlex),
  "com.spotify.tv.android": bundled(siSpotify),
  "com.wbd.stream": bundled(siHbomax),
  "fr.francetv.pluzz": shipped("france.tv", "france-tv"),
  "fr.francetv.zouzous": shipped("Okoo", "okoo"),
  "fr.tf1.mytf1": shipped("TF1+", "tf1-plus"),
  "org.videolan.vlc": bundled(siVlcmediaplayer),
  "tv.arte.plus7": shipped("ARTE", "arte"),
};

const HEX = /^[0-9A-F]{6}$/;
// Only a file of the app's own logo folder: no other path, no scheme, no network.
const LOCAL_LOGO = /^\/app-logos\/[a-z0-9-]+\.svg$/;

function valid(logo: AppLogo): boolean {
  if (logo.kind === "mark") {
    return logo.path.trim() !== "" && HEX.test(logo.hex);
  }
  return LOCAL_LOGO.test(logo.src);
}

export function resolveAppLogo(packageName: string, catalogue: AppLogoCatalogue): AppLogo | null {
  const own = Object.prototype.hasOwnProperty.call(catalogue, packageName);
  const logo = own ? catalogue[packageName] : undefined;
  if (logo === null || logo === undefined || !valid(logo)) {
    return null;
  }
  return logo;
}

export function appLogo(packageName: string): AppLogo | null {
  return resolveAppLogo(packageName, LOGOS);
}

// Every package that has a mark: what the tests check against the files and the documentation.
export function catalogedPackages(): readonly string[] {
  return Object.keys(LOGOS);
}
