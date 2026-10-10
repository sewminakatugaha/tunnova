# Proposed architecture

```text
┌───────────────────────────────┐
│ Python / GTK4 frontend        │ (regular user, never root)
│ Profiles, status, diagnostics │
└──────────────┬────────────────┘
               │ narrowly scoped authenticated IPC (future)
┌──────────────▼────────────────┐
│ root-owned networking helper  │ (future, polkit/systemd review)
│ validation, start/stop, audit │
└──────────────┬────────────────┘
               │ generates/configures process in controlled dir
┌──────────────▼────────────────┐
│ sing-box (external binary)    │ (future)
│ VLESS / TLS / WS, TUN         │
└──────────────┬────────────────┘
               │
        kernel, DNS, routes
```

## Boundaries

1. No `sudo` GUI and no privilege escalation inside the desktop process.
2. The privileged helper must expose only narrowly specified actions; no arbitrary command execution, shell expansion, unrestricted path writes, or user-provided command flags.
3. Use systemd ownership and polkit authorization for privileged operations after a formal threat-model review; the first milestone implements neither.
4. Connection profiles are sensitive. Avoid logging VLESS credentials, URLs or tokens. Plan secret storage with libsecret or an equivalent system keyring.
5. The networking engine's upstream version and configuration schema must be pinned and checked. Validate the engine configuration with its own `check` command before activation.
6. Recovery is as important as startup: network state ownership, cleanup on crash, routing-loop prevention, and behavior during Wi-Fi ↔ hotspot transitions.
7. Cross-distro packaging needs separate installers/tests. Do not assume NetworkManager, systemd or Polkit behave identically everywhere.
8. Test IPv4/IPv6, UDP/TCP, DNS, hotspot transitions, route leaks, and reboot recovery before claiming system-wide protection.

## Why GTK4 and Python for the frontend?

GTK4/PyGObject is readily available through common Linux repositories and lightweight enough for the initial GUI. The network helper may be implemented in a more restricted language/runtime after security design, rather than introducing root-level Python network mutation now.

## Phase 0 boundaries

The included app can parse and preview one link format *offline*. It cannot connect. No networking components have been installed by this scaffold.
