# Android test signing identity

Android updates an installed app in place — keeping its data, preferences, chosen TV and
home-screen widget — **only when the new APK is signed with the same certificate**. Until now
every debug APK was signed with its builder's own debug key: a new key on every CI run (five CI
builds checked on 2026-10-17 had five different certificates) and another one on each
developer machine. Each new build therefore needed an uninstall, which removes the widget and
the app's data.

This document sets up **one stable test identity** for the debug APK. It is a test key, not a
release key: it is never used for a store, and it changes nothing for the Ubuntu package.

## How it works

| Piece                                                 | Where                                                                                                                     | Secret? |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------- |
| Keystore (PKCS#12, alias `control-tv-test`, RSA 3072) | the owner's machine, outside the repository (for example `~/.config/control-tv/android-test.p12`), plus an offline backup | **yes** |
| Keystore password                                     | the owner's password manager                                                                                              | **yes** |
| CI copy of both                                       | repository secrets `CONTROL_TV_ANDROID_TEST_KEYSTORE_B64` and `CONTROL_TV_ANDROID_TEST_KEYSTORE_PASSWORD`                 | **yes** |
| Certificate SHA-256 (public)                          | `packaging/android/test-signing-certificate.sha256`, committed                                                            | no      |

- Gradle (`src-tauri/gen/android/app/build.gradle.kts`) signs debug builds with the key when
  `CONTROL_TV_ANDROID_KEYSTORE` and `CONTROL_TV_ANDROID_KEYSTORE_PASSWORD` are both set, and
  refuses to configure when only one is set or the keystore file is missing. With neither, the
  builder's debug key is used, as before.
- CI decodes the keystore only inside the APK build step, with mode 0600 in the runner's
  temporary directory, and deletes it when the step ends. Only that step receives the secrets;
  nothing prints them (GitHub also masks them).
- `scripts/android_test_signing.py verify-apk` then checks the signer:
  - stable key used and certificate equal to the pin: **accepted** (`pinned-test-key`);
  - stable key used and another certificate, or no pin committed: **CI fails** — a replaced key
    never passes silently;
  - no stable key (secret unavailable, for example a pull request from a fork): accepted as
    `ephemeral-debug-key` and labelled as such, **except on a push to `main` once a pin exists**,
    which fails.
- The artifact carries `apk-provenance.json` (commit, CI run, APK SHA-256, signer certificate,
  signing identity), and the step summary shows the same values. Before a recipe,
  `check-apk --expect-commit … --expect-run-id … --expect-apk-sha256 …` refuses any APK that its
  provenance and the expected run do not name, even one signed with the same key. The published
  digest is mandatory: the provenance travels with the APK, so only the digest copied from the CI
  run's log is independent of it; without it, or with an unreadable or malformed pinned
  certificate, the check is BLOCKED (exit 2).

## Owner setup (once)

Nothing here can be done by an agent: the key and its password stay with the owner.

1. Choose a password of at least 12 characters, keep it in a password manager, and export it in
   the shell only for the next command (it is read from the environment, never from the command
   line):

   ```bash
   read -rs CONTROL_TV_ANDROID_KEYSTORE_PASSWORD && export CONTROL_TV_ANDROID_KEYSTORE_PASSWORD
   .venv/bin/python scripts/android_test_signing.py generate \
     --keystore ~/.config/control-tv/android-test.p12
   ```

   The command refuses to run if the keystore already exists (an existing key is never
   replaced) and prints only the certificate SHA-256.

2. Back the keystore up offline. **Losing it means one more uninstall on every device.**
3. Commit the printed digest as the only line of
   `packaging/android/test-signing-certificate.sha256` (public data), in a pull request.
4. Store the CI secrets without echoing them:

   ```bash
   base64 -w0 ~/.config/control-tv/android-test.p12 \
     | gh secret set CONTROL_TV_ANDROID_TEST_KEYSTORE_B64
   printf '%s' "$CONTROL_TV_ANDROID_KEYSTORE_PASSWORD" \
     | gh secret set CONTROL_TV_ANDROID_TEST_KEYSTORE_PASSWORD
   unset CONTROL_TV_ANDROID_KEYSTORE_PASSWORD
   ```

5. Check the next CI run: its summary must say `signing identity: pinned-test-key` with the
   pinned digest.

Local builds use the same key by exporting `CONTROL_TV_ANDROID_KEYSTORE` (the keystore path) and
the password before `python3 scripts/dev.py android-apk`.

## Migration from the APKs already installed

- Every APK installed so far is signed with a CI run's or a machine's debug key. CI keys are
  discarded with their runner, and the debug APKs use signature scheme v2 only (no v3 rotation
  lineage), so **no update path exists from them**: moving to the stable identity needs **one
  last uninstall** on each phone. This removes the app's data: the chosen TV, the widget and its
  TV, and the remote-channel pairing (the phone was not paired as of 2026-10-17).
- After that single reinstall, every later build signed with the pinned identity updates in
  place: data, preferences, pairing and widget are kept.
- Before uninstalling, note the chosen TV and the widget's position, so they can be restored.
- To check which identity an APK file has, without installing it:
  `.venv/bin/python scripts/android_test_signing.py verify-apk --apk APK --apksigner "$ANDROID_HOME/build-tools/<version>/apksigner"`.

## Limits

- Android also refuses a lower `versionCode`; all builds use 1000, so builds of the same version
  replace each other in place (a newer build is not distinguished by `versionCode`).
- A different key, a lost keystore, or a compromised one each require an uninstall again (or a
  v3 key rotation, which this project does not set up).
- The identity proves which key signed the APK, not which commit it was built from: that comes
  from `apk-provenance.json` and the CI run.
- Ephemeral builds remain possible (no secret); they are labelled and must not be installed
  over a pinned build.
