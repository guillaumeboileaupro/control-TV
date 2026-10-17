// Central catalogue of locally bundled brand marks for exact Android package identifiers.
// Unknown or invalid entries deliberately resolve to null: callers retain their initial fallback.

import {
  siAppletv,
  siGoogleplay,
  siGoogletv,
  siMubi,
  siNetflix,
  siPlex,
  siSpotify,
  siVlcmediaplayer,
  siYoutube,
  siYoutubekids,
  siYoutubemusic,
  type SimpleIcon,
} from "simple-icons";

export interface AppLogo {
  readonly title: string;
  readonly path: string;
  readonly hex: string;
}

export type AppLogoCatalogue = Readonly<Record<string, AppLogo | null>>;

function bundled(icon: SimpleIcon): AppLogo {
  return { title: icon.title, path: icon.path, hex: icon.hex };
}

const LOGOS: AppLogoCatalogue = {
  "com.apple.atve.androidtv.appletv": bundled(siAppletv),
  "com.android.vending": bundled(siGoogleplay),
  "com.google.android.apps.tv.launcherx": bundled(siGoogletv),
  "com.google.android.youtube.tv": bundled(siYoutube),
  "com.google.android.youtube.tvkids": bundled(siYoutubekids),
  "com.google.android.youtube.tvmusic": bundled(siYoutubemusic),
  "com.mubi": bundled(siMubi),
  "com.netflix.ninja": bundled(siNetflix),
  "com.plexapp.android": bundled(siPlex),
  "com.spotify.tv.android": bundled(siSpotify),
  "org.videolan.vlc": bundled(siVlcmediaplayer),
};

const HEX = /^[0-9A-F]{6}$/;

export function resolveAppLogo(packageName: string, catalogue: AppLogoCatalogue): AppLogo | null {
  const logo = catalogue[packageName];
  if (logo === null || logo === undefined || logo.path.trim() === "" || !HEX.test(logo.hex)) {
    return null;
  }
  return logo;
}

export function appLogo(packageName: string): AppLogo | null {
  return resolveAppLogo(packageName, LOGOS);
}
