# TV application logos

The application grid resolves an exact Android package identifier through `ui/src/app-logos.ts`.
There is no name matching, network lookup or CDN. An unknown package, or an invalid catalogue
entry, uses the existing deterministic initial.

Two kinds of mark exist: a single-colour path from Simple Icons, compiled into the JavaScript, or
an official logo file shipped with the application under `assets/app-logos/` (served locally at
`/app-logos/`, drawn in an `<img>`, never fetched from the network). The catalogue only accepts a
file of that folder.

The Simple Icons paths below come from Simple Icons 16.33.0 and are tree-shaken into the application bundle.
Simple Icons is distributed as CC0-1.0, but that does not grant trademark rights and individual
entries can have their own terms. Brand marks are shown only to identify the corresponding app.

| Displayed mark   | Android package identifiers            | Upstream source or guidelines                                                                                                                                        |
| ---------------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Apple TV         | `com.apple.atve.androidtv.appletv`     | [Simple Icons source](<https://en.wikipedia.org/wiki/File:Apple_TV_(logo).svg>)                                                                                      |
| Google Play      | `com.android.vending`                  | [Google Play brand resources](https://partnermarketinghub.withgoogle.com/brands/google-play/visual-identity/primary-logos/)                                          |
| Google TV        | `com.google.android.apps.tv.launcherx` | [Google TV brand resources](https://partnermarketinghub.withgoogle.com/brands/google-tv/visual-identity/visual-identity/)                                            |
| MUBI             | `com.mubi`                             | [MUBI](https://mubi.com/)                                                                                                                                            |
| Netflix          | `com.netflix.ninja`                    | [Netflix brand assets](https://brand.netflix.com/en/assets/logos)                                                                                                    |
| Plex             | `com.plexapp.android`                  | [Plex brand assets](https://brand.plex.tv/)                                                                                                                          |
| Spotify          | `com.spotify.tv.android`               | [Spotify branding](https://developer.spotify.com/documentation/design)                                                                                               |
| VLC media player | `org.videolan.vlc`                     | [VideoLAN source asset](https://code.videolan.org/videolan/vlc/-/blob/1ce7f686ee17a028d2d79627ae69f22d905f2e23/extras/package/macosx/asset_sources/vlc_app_icon.svg) |
| YouTube          | `com.google.android.youtube.tv`        | [YouTube brand resources](https://www.youtube.com/howyoutubeworks/resources/brand-resources/)                                                                        |
| YouTube Kids     | `com.google.android.youtube.tvkids`    | [YouTube Kids](https://www.youtube.com/intl/ALL_us/kids/)                                                                                                            |
| YouTube Music    | `com.google.android.youtube.tvmusic`   | [Google partner brand resources](https://partnermarketinghub.withgoogle.com/)                                                                                        |
| Google TV        | `com.google.android.videos`            | Google Play lists this package as "Google TV" (2026-10-17)                                                                                                           |
| Paramount+       | `com.cbs.ca`                           | Google Play lists this package as "Paramount+" (2026-10-17)                                                                                                          |
| HBO Max          | `com.wbd.stream`                       | Google Play lists this package as "HBO Max" (2026-10-17)                                                                                                             |

## Official logo files (`assets/app-logos/`)

Simple Icons 16.33.0 has no mark for these apps. Each file comes from Wikimedia Commons, where it
is classified as **public domain** (a text logo below the threshold of originality, "PD-textlogo"
or "PD-ineligible") or dedicated to **CC0**; Commons records each as trademarked. No attribution is
legally required; the source is kept here for provenance. Trademarks remain their owners'; the
marks only identify the app. Files were checked on 2026-10-17, then stripped of editor metadata and
verified to hold no script, foreign content, external or data reference (`tests/test_app_logo_assets.py`).
The drawing itself is unchanged.

| File                    | Displayed mark    | Android package                     | Commons source                                                                                                   | Licence                                             | Note                                                                               |
| ----------------------- | ----------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `arte.svg`              | ARTE              | `tv.arte.plus7`                     | [Arte Logo 2026.svg](https://commons.wikimedia.org/wiki/File:Arte_Logo_2026.svg)                                 | Public domain (PD-textlogo)                         | current positioning of the ARTE wordmark                                           |
| `canal-plus.svg`        | CANAL+            | `com.canal.android.canal`           | [Logo Canal+ 1995.svg](https://commons.wikimedia.org/wiki/File:Logo_Canal%2B_1995.svg)                           | Public domain (PD-textlogo)                         | Étienne Robial's CANAL+ wordmark; **owner to confirm visually** it matches the app |
| `disney-plus.svg`       | Disney+           | `com.disney.disneyplus`             | [Disney+ logo.svg](https://commons.wikimedia.org/wiki/File:Disney%2B_logo.svg)                                   | Public domain (PD-textlogo)                         | 2019 version of the wordmark; **owner to confirm visually**                        |
| `france-tv.svg`         | france.tv         | `fr.francetv.pluzz`                 | [France.tv - logo 2022.svg](https://commons.wikimedia.org/wiki/File:France.tv_-_logo_2022.svg)                   | Public domain (PD-textlogo)                         | traced from france.tv's own SVG                                                    |
| `google-play-games.svg` | Google Play Games | `com.google.android.play.games`     | [Google Play Games logo (2023).svg](<https://commons.wikimedia.org/wiki/File:Google_Play_Games_logo_(2023).svg>) | Public domain (PD-ineligible)                       | the Play Games mark itself, not the Play Store's                                   |
| `molotov.svg`           | Molotov           | `com.molotov.app`                   | [Logo Molotov.tv.svg](https://commons.wikimedia.org/wiki/File:Logo_Molotov.tv.svg)                               | Public domain (PD-textlogo)                         | white letters: drawn on a dark tile, colours unchanged                             |
| `prime-video.svg`       | Prime Video       | `com.amazon.amazonvideo.livingroom` | [Prime Video logo (2024).svg](<https://commons.wikimedia.org/wiki/File:Prime_Video_logo_(2024).svg>)             | Public domain (PD-textlogo)                         | logo in use since 2024                                                             |
| `tf1-plus.svg`          | TF1+              | `fr.tf1.mytf1`                      | [Logo TF1+.svg](https://commons.wikimedia.org/wiki/File:Logo_TF1%2B.svg)                                         | CC0 (uploader's reproduction of the TF1+ text logo) | **owner to confirm visually**                                                      |
| `okoo.svg`              | Okoo              | `fr.francetv.zouzous`               | [Logo Okoo 2019.svg](https://commons.wikimedia.org/wiki/File:Logo_Okoo_2019.svg)                                 | Public domain (PD-textlogo)                         | traced from france.tv's own Okoo SVG                                               |

## Apps without a mark

- **M6+** (`fr.m6.m6replay.by`): Commons has the M6 channel logo but no M6+ logo; the channel mark
  would identify a different product. Keeps the initial.
- **Android Settings** (`com.android.tv.settings`) and **Google TV recommendations**
  (`com.google.android.tvrecommendations`): the Android and Google TV marks identify different
  products. Keep the initial.
- **Freebox-specific packages** (not on Google Play, so neither their official name nor their
  logo can be verified): `fr.freebox.disneypluslauncher` (a Freebox launcher that opens Disney+;
  a launcher is not the app it opens), `fr.freebox.fbx8lc.fbxdiags` (Freebox diagnostics),
  `fr.free.aktu` and `com.nextinteractive.rmcbfmplay.tv.free` (Free editions). They keep their
  package as label and the initial.
- Any package not listed above keeps the initial. A logo must not be approximated, or copied from
  an unofficial source or a source whose licence does not allow redistribution, merely to replace
  that fallback.

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
