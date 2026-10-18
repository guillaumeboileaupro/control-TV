---
name: hardware-safety
description: Default-deny rules for TV, Freebox, Cast, ADB, phone, installation, sudo, system configuration, screen capture, network streams and GPU use, plus privacy of device data. Use before any command that could touch a device, the system or the network, and when classifying evidence.
---

# Hardware and system safety

Nothing below runs without the owner's **explicit authorization for that step** in the current
session; a general approval or an earlier session's approval does not count.

- TV / Freebox / Cast commands, discovery that contacts a device, remote-channel keys, pairing.
- ADB (enabling, connecting, `adb` commands), phone installs, developer options.
- Software or package installation, `sudo`, `apt`, `dpkg -i`, system or X11/kernel changes.
- Real screen capture, network listeners or streams, LAN exposure.
- Long or heavy hardware runs (GPU encodes) beyond a short synthetic check the mission allows.

Always allowed: reading the repository, offline tests with doubles, synthetic sources in temporary
directories, read-only inspection of local files and artifacts.

Privacy: never print, commit or put in PR text device names, IP/MAC addresses, device ids/UUIDs,
media titles or credentials; keep evidence under `.ai-private/` (git-ignored).

Evidence classes, never mixed: simulated, CI, hardware PASS / FAIL / NOT RUN. A green CI or a
simulation is never a hardware result; a receiver confirmation is not a physical effect.
