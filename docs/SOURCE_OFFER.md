# Source code of control-TV and of the free software it contains

control-TV is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version (`GPL-3.0-or-later`; text in `LICENSE`, installed
as `/usr/share/doc/control-tv/copyright`). It comes with no warranty.

This page is the source offer that accompanies every control-TV package (`.deb` and APK). It is an
engineering document, not legal advice.

## Where the corresponding source is

The packages of a release are built from the public Git tag of that release, for example
`v0.2.0`, of <https://github.com/guillaumeboileaupro/control-TV>. That tag holds the complete
source of control-TV and the scripts that build every package (`scripts/dev.py`, `packaging/`,
the lockfiles). The release page of the same tag keeps, next to the packages, the source
archives listed below for the components whose license asks for them. They stay available for
at least three years after the release, at no charge.

| Component | License | Exact source |
|---|---|---|
| control-TV | GPL-3.0-or-later | the release tag |
| zeroconf 0.151.3 | LGPL-2.1-or-later | `zeroconf-0.151.3.tar.gz`, attached to the release (also on PyPI, SHA-256 `ce6c548e665759b6150cef4db9ab9d7bdd89857e90c513abd6b7340bdd7dbd6a`) |
| certifi | MPL-2.0 | the PyPI source archive of the version pinned in `uv.lock` |
| MPL-2.0 Rust crates | MPL-2.0 | crates.io, versions pinned in `src-tauri/Cargo.lock` |
| PyInstaller bootloader 6.22.3 | GPL-2.0-or-later with the bootloader exception | the PyPI source archive of PyInstaller 6.22.3 |
| libstdc++, libgcc_s (GCC 12) | GPL-3.0-or-later with the GCC Runtime Library Exception | Ubuntu 22.04's `gcc-12` source package (`apt-get source gcc-12`) |
| CPython 3.12.15 and the libraries linked into it | PSF-2.0 and permissive licenses | python-build-standalone release `20261003` and CPython `v3.12.15` |

The license text of every component is in the package: `python-bridge/licenses/` for the Python
bridge and `/usr/share/doc/control-tv/rust-licenses/` for the Rust application.

## Using a modified zeroconf (LGPL-2.1, section 6)

zeroconf's compiled modules are separate files of the bridge; its pure-Python modules are inside
the frozen bridge archive. To run control-TV with a modified zeroconf, rebuild the package from
the release tag with your version:

```bash
git clone --branch v0.2.0 https://github.com/guillaumeboileaupro/control-TV.git
cd control-TV
# replace zeroconf with your modified source, for example:
uv add --frozen ./path/to/your/zeroconf
python3 scripts/dev.py release-deb   # builds dist/control-tv_<version>_amd64.deb with it
sudo dpkg -i dist/control-tv_*_amd64.deb
```

The build is the one the project's CI runs (`README.md`, "Linux release package"); it needs the
tools named there (uv, Rust, Node.js).
