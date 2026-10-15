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
               │ starts Xray with in-memory config (unprivileged)
┌──────────────▼────────────────┐
│ Xray-core (external binary)   │ (explicitly configured executable)
│ VLESS / TLS / WebSocket       │
└──────────────┬────────────────┘
               │
        kernel, DNS, routes
```

## Boundaries

1. No `sudo` GUI and no privilege escalation inside the desktop process.
2. The privileged helper must expose only narrowly specified actions; no arbitrary command execution, shell expansion, unrestricted path writes, or user-provided command flags.
3. Use systemd ownership and polkit authorization for privileged operations after a formal threat-model review; the first milestone implements neither.
4. Connection profiles are sensitive. The current profile manager keeps them in memory only. Display only the profile name, server address, port, transport and TLS status; never display complete VLESS links, UUIDs or credentials in logs or error messages. Plan secret storage with libsecret or an equivalent system keyring before adding persistence.
5. Xray configuration is built as a JSON-compatible in-memory object from a validated profile. Its field mapping follows the official [VLESS](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/outbounds/vless.md), [SOCKS inbound](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/inbounds/socks.md), [transport/TLS](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/transport.md), [TLS](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/transports/tls.md) and [WebSocket](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/transports/websocket.md) references. `XrayProcessManager` takes an explicitly configured executable path and passes bounded JSON through stdin using Xray's `stdin:` configuration source; credential-bearing configuration is never written to disk. It discards child output, reports only sanitized lifecycle errors, rejects an already occupied SOCKS port before launch, and probes readiness only on the configured `127.0.0.1` listener while verifying the child remains alive. Startup and shutdown state transitions are synchronized without holding the lock over blocking I/O; shutdown escalates from graceful termination to forced termination and cleans the Linux process group where possible. It does not install Xray, alter routes/DNS/firewall/interfaces, or enable the GTK Connect button. Before product activation, pin and verify the engine version and schema and validate the configuration with Xray-core's `run -test -format json -config stdin:` command.
6. Recovery is as important as startup: network state ownership, cleanup on crash, routing-loop prevention, and behavior during Wi-Fi ↔ hotspot transitions.
7. Cross-distro packaging needs separate installers/tests. Do not assume NetworkManager, systemd or Polkit behave identically everywhere.
8. Test IPv4/IPv6, UDP/TCP, DNS, hotspot transitions, route leaks, and reboot recovery before claiming system-wide protection.

## Why GTK4 and Python for the frontend?

GTK4/PyGObject is readily available through common Linux repositories and lightweight enough for the initial GUI. The network helper may be implemented in a more restricted language/runtime after security design, rather than introducing root-level Python network mutation now.

## Phase 0 boundaries

The included app can import, list, select and remove supported VLESS + WebSocket + TLS profiles *offline* for the current session. The Xray configuration generator maps a validated profile into a VLESS outbound and a SOCKS5 inbound bound to `127.0.0.1`; TLS certificate verification remains enabled. An optional `tls_verify_name` configuration may set Xray's `verifyPeerCertByName` independently of the profile's TLS SNI (`serverName`) and WebSocket Host. It is never inferred from the server address. Configure it only when the hostname is known and trusted as the intended certificate identity; an incorrect name should fail verification rather than disable it. The standalone process manager can start a separately installed Xray executable with that config over stdin and supervise only the local listener. The GUI does not call the manager, profiles are not persisted, and no networking components are installed or system networking settings modified by this scaffold.
