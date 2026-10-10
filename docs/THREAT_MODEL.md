# Threat model (initial)

**Protect:** proxy credentials, user browsing metadata, host network state, process privileges, update/download integrity.

**Trust boundaries:** untrusted imported URI → frontend parser; GUI → privileged helper; helper → engine; local machine → network.

**Risks & mitigations:**

- Malicious profile: strict parsing, bounded input, reject unsupported variants, never run user-provided commands.
- Credentials leak: do not print imported URI; redact in diagnostics; use OS keyring when storage is implemented.
- Privilege escalation: no root GUI; strictly authorized D-Bus/Polkit methods, helper ownership and packaging review.
- Traffic loop or DNS leak: prevent tunnel start without safe upstream routing; test shutdown and network switching; document unsupported protocols.
- VPN claiming connectivity falsely: distinguish daemon running, server reachable, DNS working, and actual routed traffic.
- Supply chain: verify upstream binaries and hashes; pin version; publish source and build information.
- Crash cleanup: remove only routes and rules owned by our service; preserve users' unrelated network configuration.

**NOT yet implemented:** privileged helper, engine, protected secret storage, leak-safe routing, installer or real VPN. Do not use as a privacy/security product yet.
