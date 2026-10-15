// Locally bundled brand marks for TV packages we can identify exactly. Unknown packages keep
// the existing deterministic initial: no network lookup and no guessed logo.

import {
  siAppletv,
  siGoogleplay,
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

const LOGOS: Readonly<Record<string, SimpleIcon>> = {
  "com.apple.atve.androidtv.appletv": siAppletv,
  "com.android.vending": siGoogleplay,
  "com.google.android.play.games": siGoogleplay,
  "com.google.android.youtube.tv": siYoutube,
  "com.google.android.youtube.tvkids": siYoutubekids,
  "com.google.android.youtube.tvmusic": siYoutubemusic,
  "com.mubi": siMubi,
  "com.netflix.ninja": siNetflix,
  "com.plexapp.android": siPlex,
  "com.spotify.tv.android": siSpotify,
  "org.videolan.vlc": siVlcmediaplayer,
};

export function appLogo(packageName: string): SimpleIcon | null {
  return LOGOS[packageName] ?? null;
}
