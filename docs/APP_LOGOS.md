# TV application logos

The application grid resolves an exact Android package identifier through `ui/src/app-logos.ts`.
There is no name matching, network lookup or CDN. An unknown package, or an invalid catalogue
entry, uses the existing deterministic initial.

The paths below come from Simple Icons 16.33.0 and are tree-shaken into the application bundle.
Simple Icons is distributed as CC0-1.0, but that does not grant trademark rights and individual
entries can have their own terms. Brand marks are shown only to identify the corresponding app.

| Displayed mark | Android package identifiers | Upstream source or guidelines |
|---|---|---|
| Android | `com.android.tv.settings` | [Android brand guidelines](https://developer.android.com/distribute/marketing-tools/brand-guidelines#brand-android); icon metadata declares CC-BY-3.0 |
| Apple TV | `com.apple.atve.androidtv.appletv` | [Simple Icons source](https://en.wikipedia.org/wiki/File:Apple_TV_(logo).svg) |
| Google Play | `com.android.vending`, `com.google.android.play.games` | [Google Play brand resources](https://partnermarketinghub.withgoogle.com/brands/google-play/visual-identity/primary-logos/) |
| Google TV | `com.google.android.apps.tv.launcherx`, `com.google.android.tvrecommendations` | [Google TV brand resources](https://partnermarketinghub.withgoogle.com/brands/google-tv/visual-identity/visual-identity/) |
| MUBI | `com.mubi` | [MUBI](https://mubi.com/) |
| Netflix | `com.netflix.ninja` | [Netflix brand assets](https://brand.netflix.com/en/assets/logos) |
| Plex | `com.plexapp.android` | [Plex brand assets](https://brand.plex.tv/) |
| Spotify | `com.spotify.tv.android` | [Spotify branding](https://developer.spotify.com/documentation/design) |
| VLC media player | `org.videolan.vlc` | [VideoLAN source asset](https://code.videolan.org/videolan/vlc/-/blob/1ce7f686ee17a028d2d79627ae69f22d905f2e23/extras/package/macosx/asset_sources/vlc_app_icon.svg) |
| YouTube | `com.google.android.youtube.tv` | [YouTube brand resources](https://www.youtube.com/howyoutubeworks/resources/brand-resources/) |
| YouTube Kids | `com.google.android.youtube.tvkids` | [YouTube Kids](https://www.youtube.com/intl/ALL_us/kids/) |
| YouTube Music | `com.google.android.youtube.tvmusic` | [Google partner brand resources](https://partnermarketinghub.withgoogle.com/) |

Prime Video, Disney+, CANAL+, france.tv, M6+, TF1+, ARTE and Molotov retain the initial fallback:
Simple Icons 16.33.0 contains no corresponding mark, and this repository has no separately
licensed official asset for them. A logo must not be approximated or copied from an unofficial
source merely to replace that fallback.

The production build rejects more than 64 KiB of uncompressed application JavaScript. This guards
the targeted imports against accidentally bundling the complete Simple Icons catalogue.
