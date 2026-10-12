# Tunnova

**Connect freely. Stay in control.**

> **Early, offline-only prototype. It does not establish a VPN connection yet.**

Tunnova is a community-driven, beginner-friendly, open-source Linux VPN desktop client. Our goal is straightforward: **install, import a supported configuration, connect — without terminal troubleshooting**.

## Project identity

- **Name:** Tunnova
- **Tagline:** Connect freely. Stay in control.
- **Official repository:** https://github.com/sewminakatugaha/tunnova
- **Python package and CLI:** `tunnova`
- **Current version:** `0.1.0.dev0` (prototype only)
- **Initial platform:** Linux Mint and Ubuntu

See [identity and naming notes](docs/IDENTITY.md).

## Project status

**Current implementation: offline VLESS profile manager.** The app imports supported VLESS + WebSocket + TLS links locally, lists profiles for the current session, rejects duplicates, and lets users select and remove profiles. Profile data is kept in memory only and is not permanently stored. Its **Connect** button is deliberately disabled. It does not modify network interfaces, routes, DNS, firewall rules, proxies, or systemd units.

Tunnova is the official project working name. This codebase is an offline profile-manager prototype, **not a working VPN or a public release**. Testing, package distribution, and trademark checks are separate milestones.

## Why build it?

Too many Linux users encounter opaque VPN errors, missing permissions, broken system proxy coverage, and network switching failures. We want a tool that explains failures, supports safe recovery, and installs without hand-written network commands.

## Local development — Linux Mint / Ubuntu

1. Install GTK4 bindings from the distribution:

   ```bash
   sudo apt update
   sudo apt install python3 python3-gi gir1.2-gtk-4.0
   ```

2. From this repository's root, run:

   ```bash
   python3 -m tunnova
   ```

3. Try this **synthetic, non-working** link in the profile import field (do not use a real credential in a bug report):

   ```text
   vless://11111111-2222-4333-8444-555555555555@example.com:443?type=ws&security=tls&path=%2Fedge&sni=example.com#Demo
   ```

4. Run the unit tests (no network and no GTK required):

   ```bash
   python3 -m unittest discover -s tests -v
   ```

5. Check the version:

   ```bash
   python3 -m tunnova --version
   ```

Imported profiles are available only until the application exits. Do **not** paste real VLESS links into GitHub issues, logs, screenshots, or source control; links can contain credentials.

## Technical direction (proposal)

- Unprivileged Python + GTK4 desktop UI.
- Separate, narrowly privileged networking helper managed by systemd and authorized with polkit (to be designed and security-reviewed).
- **sing-box** as an external VPN/proxy engine (not bundled in this prototype).
- Linux TUN / routing with deliberate protection against loops, DNS leaks, and unsafe cleanup.
- NetworkManager integration and diagnostics that do not expose private configuration details.
- Linux Mint/Ubuntu first, then Fedora and Arch once tested.

See [architecture](docs/ARCHITECTURE.md), [roadmap](docs/ROADMAP.md), and [security model](docs/THREAT_MODEL.md).

## Contribution

Before submitting code, read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). CI runs parser unit tests on each proposed change. No user proxy configurations are required for testing.

## License

GPL-3.0-or-later for the starter project (see [LICENSE](LICENSE)). This is a **proposed initial licensing choice** and can be reviewed with contributors before public launch. Third-party projects, including sing-box, retain their own licenses and conditions; see [NOTICE.md](NOTICE.md).

## Disclaimer

Network transport coverage, support for VLESS/VMess/etc., TUN reliability, and cross-distribution installation are **goals**, not shipped capabilities. This project is not affiliated with Hiddify or SagerNet.
