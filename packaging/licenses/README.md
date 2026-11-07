# License texts kept for the release packages

The release build copies these texts into the packages because the components they cover ship
none of their own (see `docs/PACKAGING_LICENSES.md`). They are copied as published, unchanged.

- `libpython/`: the libraries python-build-standalone links statically into `libpython`
  (OpenSSL, SQLite, zlib, libedit, libffi, xz/liblzma, bzip2, ncurses, mpdecimal, expat,
  libuuid), taken from `python/licenses/` of the full archive
  `cpython-3.12.15+20261003-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst` (the build whose
  `libpython` the bridge ships, compiled 2026-10-03 with OpenSSL 3.5.9). Tcl/Tk, X11 and Berkeley
  DB are not in that `libpython`, so their texts are not kept. `LICENSE.hacl.txt` is the MIT
  notice at the top of CPython v3.12.15's `Modules/_hacl/Hacl_Hash_SHA2.c`.
- `rust/`: the crates whose published package ships no license file, from their upstream
  repositories (`unic/` serves the five `unic-*` crates of one repository); `selectors/` is the
  official MPL-2.0 text from mozilla.org.

When `packaging/release-python-version` or the python-build-standalone build changes, take the
texts again from the matching full archive.
