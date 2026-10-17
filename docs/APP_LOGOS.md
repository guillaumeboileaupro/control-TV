# TV application logos

The application grid resolves an exact Android package identifier through `ui/src/app-logos.ts`.
There is no name matching, network lookup or CDN. An unknown package, or an invalid catalogue
entry, uses the existing deterministic initial.

The paths below come from Simple Icons 16.33.0 and are tree-shaken into the application bundle.
Simple Icons is distributed as CC0-1.0, but that does not grant trademark rights and individual
entries can have their own terms. Brand marks are shown only to identify the corresponding app.

| Displayed mark | Android package identifiers | Upstream source or guidelines |
|---|---|---|
| Apple TV | `com.apple.atve.androidtv.appletv` | [Simple Icons source](https://en.wikipedia.org/wiki/File:Apple_TV_(logo).svg) |
| Google Play | `com.android.vending` | [Google Play brand resources](https://partnermarketinghub.withgoogle.com/brands/google-play/visual-identity/primary-logos/) |
| Google TV | `com.google.android.apps.tv.launcherx` | [Google TV brand resources](https://partnermarketinghub.withgoogle.com/brands/google-tv/visual-identity/visual-identity/) |
| MUBI | `com.mubi` | [MUBI](https://mubi.com/) |
| Netflix | `com.netflix.ninja` | [Netflix brand assets](https://brand.netflix.com/en/assets/logos) |
| Plex | `com.plexapp.android` | [Plex brand assets](https://brand.plex.tv/) |
| Spotify | `com.spotify.tv.android` | [Spotify branding](https://developer.spotify.com/documentation/design) |
| VLC media player | `org.videolan.vlc` | [VideoLAN source asset](https://code.videolan.org/videolan/vlc/-/blob/1ce7f686ee17a028d2d79627ae69f22d905f2e23/extras/package/macosx/asset_sources/vlc_app_icon.svg) |
| YouTube | `com.google.android.youtube.tv` | [YouTube brand resources](https://www.youtube.com/howyoutubeworks/resources/brand-resources/) |
| YouTube Kids | `com.google.android.youtube.tvkids` | [YouTube Kids](https://www.youtube.com/intl/ALL_us/kids/) |
| YouTube Music | `com.google.android.youtube.tvmusic` | [Google partner brand resources](https://partnermarketinghub.withgoogle.com/) |

Google Play Games, Android Settings and the Google TV recommendations service retain the initial
fallback because the available Google Play, Android and Google TV marks identify different
products. Prime Video, Disney+, CANAL+, france.tv, M6+, TF1+, ARTE and Molotov also retain the
fallback: Simple Icons 16.33.0 contains no corresponding mark, and this repository has no
separately licensed official asset for them. A logo must not be approximated or copied from an
unofficial source merely to replace that fallback.

## Dependency assessment

Simple Icons is retained because it provides versioned, optimized paths and source metadata for
the exact marks above. Version 16.33.0 has no transitive runtime dependency. Its installed contents
occupy 16,230,532 bytes (about 26 MiB of allocated local disk), while Vite tree-shakes the named
imports: the complete frontend JavaScript bundle is measured by the build and remains below its
65,536-byte limit (56,745 bytes at this revision, leaving 8,791 bytes of headroom). Replacing these
imports with copied paths would remove the development install
footprint but would not materially reduce the compiled paths shipped to users, and would move
upstream provenance and updates into this repository.

The package is CC0-1.0, but its disclaimer says individual icons may have separate terms and no
trademark rights are granted. Each catalogue change therefore still requires an exact product
identity and a review of the icon's current source and brand guidelines.

The production build rejects more than 64 KiB of uncompressed application JavaScript. This guards
the targeted imports against accidentally bundling the complete Simple Icons catalogue.
