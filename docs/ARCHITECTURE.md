# Proposed architecture

```text
┌───────────────────────────────┐
│ Python / GTK4 frontend        │ (regular user, never root)
│ Profiles, status, diagnostics │
└──────────────┬────────────────┘
                 │ future async state API
┌──────────────▼────────────────┐
│ ConnectionController          │ (unprivileged, Python)
│ config + Xray lifecycle       │
└──────────────┬────────────────┘
                 │ starts Xray with in-memory config
┌──────────────▼────────────────┐
│ Xray-core (external binary)   │ (explicitly configured executable)
│ VLESS / TLS / WebSocket       │
└──────────────┬────────────────┘
                 │ local SOCKS5 only
         127.0.0.1 listener

Future system-wide routing may require a separately reviewed privileged helper
(polkit/systemd); the controller does not invoke one or modify system networking.
```

## Boundaries

1. No `sudo` GUI and no privilege escalation inside the desktop process.
2. The privileged helper must expose only narrowly specified actions; no arbitrary command execution, shell expansion, unrestricted path writes, or user-provided command flags.
3. Use systemd ownership and polkit authorization for privileged operations after a formal threat-model review; the first milestone implements neither.
4. Connection profiles are sensitive. The current profile manager keeps them in memory only. Display only the profile name, server address, port, transport and TLS status; never display complete VLESS links, UUIDs or credentials in logs or error messages. Plan secret storage with libsecret or an equivalent system keyring before adding persistence.
5. `ConnectionController` accepts a validated in-memory profile, explicit Xray executable path, local SOCKS port and optional trusted TLS verification name. It delegates config creation to `build_xray_config` and process start/stop/readiness to `XrayProcessManager`; it never derives a TLS verification name from the profile server. Its Future-returning connect/disconnect operations do not block the caller, prevent duplicate starts, and coordinate disconnects that arrive during startup, including a queued connect Future canceled before its worker starts. State notifications are queued in transition order and delivered serially without holding controller locks during application callbacks. Failed cleanup leaves the controller able to retry shutdown instead of closing the executor with cleanup work outstanding. `proxy_ready` means only that Xray's local SOCKS5 handshake succeeded; it is not proof of remote internet connectivity. The controller monitors the child and reports unexpected exit as an error. The GTK frontend marshals state and operation-completion updates onto the GTK main context, locks profile controls until cleanup finishes, and waits for asynchronous shutdown before closing. It remains unprivileged and does not change system proxy, routes, DNS, firewall or interfaces.
6. Xray configuration is built as a JSON-compatible in-memory object from a validated profile. Its field mapping follows the official [VLESS](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/outbounds/vless.md), [SOCKS inbound](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/inbounds/socks.md), [transport/TLS](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/transport.md), [TLS](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/transports/tls.md) and [WebSocket](https://github.com/XTLS/Xray-docs-next/blob/f179efd35d1fda797a853ed8fa98c98235e69fb2/docs/en/config/transports/websocket.md) references. `XrayProcessManager` takes an explicitly configured executable path and passes bounded JSON through stdin using Xray's `stdin:` configuration source; credential-bearing configuration is never written to disk. It discards child output, reports only sanitized lifecycle errors, rejects an already occupied SOCKS port before launch, and probes readiness only on the configured `127.0.0.1` listener while verifying the child remains alive. Startup and shutdown state transitions are synchronized without holding the lock over blocking I/O; shutdown escalates from graceful termination to forced termination and cleans the Linux process group where possible. It does not install Xray or alter routes/DNS/firewall/interfaces. Before product activation, pin and verify the engine version and schema and validate the configuration with Xray-core's `run -test -format json -config stdin:` command.
7. Recovery is as important as startup: network state ownership, cleanup on crash, routing-loop prevention, and behavior during Wi-Fi ↔ hotspot transitions.
8. Cross-distro packaging needs separate installers/tests. Do not assume NetworkManager, systemd or Polkit behave identically everywhere.
9. Test IPv4/IPv6, UDP/TCP, DNS, hotspot transitions, route leaks, and reboot recovery before claiming system-wide protection.

## Why GTK4 and Python for the frontend?

## Controller shutdown guarantee

Disconnect waits for the connection worker to finish startup or its own cancellation cleanup before stopping Xray. The controller reports `DISCONNECTED` only after `XrayProcessManager` confirms its state is `STOPPED`; if startup does not finish within the bounded wait, the state becomes `ERROR` and shutdown can be retried. Manager start, readiness checks and stop operations are serialized by the controller, preventing a late start from escaping an earlier cleanup attempt. If a pending disconnect/shutdown Future is canceled before its worker starts, cleanup is resubmitted rather than leaving the controller in `DISCONNECTING` without an active task. Cleanup failure does not shut down the executor, so a later disconnect or shutdown can retry. State callbacks run serially outside controller locks; a callback that blocks delays later notifications, but does not block process lifecycle work. Listeners should return promptly and marshal GTK updates to the main context.

GTK4/PyGObject is readily available through common Linux repositories and lightweight enough for the initial GUI. The network helper may be implemented in a more restricted language/runtime after security design, rather than introducing root-level Python network mutation now.

## Phase 0 boundaries

The included app can import, list, select and remove supported VLESS + WebSocket + TLS profiles for the current session and connect the selected profile through an explicitly configured Xray executable. The unprivileged connection controller composes the Xray config builder and process manager and reports local SOCKS proxy readiness without claiming remote internet connectivity. The Xray configuration generator maps a validated profile into a VLESS outbound and a SOCKS5 inbound bound to `127.0.0.1:10890`; TLS certificate verification remains enabled. An optional `tls_verify_name` configuration may set Xray's `verifyPeerCertByName` independently of the profile's TLS SNI (`serverName`) and WebSocket Host. It is never inferred from the server address. Configure it only when the hostname is known and trusted as the intended certificate identity; an incorrect name should fail verification rather than disable it. Profiles are not persisted, and no networking components are installed or system networking settings modified.
