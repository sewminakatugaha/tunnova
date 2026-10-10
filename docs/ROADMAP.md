# Milestones (draft)

## M0: Foundation (this archive)

- [x] GTK4 desktop prototype with an offline VLESS WS+TLS link preview
- [x] Redact user IDs from object repr and from errors
- [x] Parser tests and Linux CI workflow
- [x] Architecture, security, contribution, issue templates, and GPL license
- [x] Adopt Tunnova as the official working project name
- [ ] Confirm repository naming and carry out appropriate trademark clearance before public launch
- [ ] Create public GitHub repository and publish M0

## M1: Safe local connections

- [ ] Design and security-review IPC/protection boundaries
- [ ] Config format conversion with fixtures and independent unit tests
- [ ] Launch an external sing-box binary in local-proxy mode (explicit opt-in)
- [ ] Import validation and status view; never log credentials
- [ ] Test basic VLESS+WS+TLS end to end with a test server

## M2: System-wide tunnel

- [ ] Privileged helper with polkit authorization
- [ ] Integrated TUN startup, DNS management, endpoint-exclusion safeguards
- [ ] NetworkManager connection-change behavior, recovery, crash cleanup
- [ ] IPv4 + IPv6 + UDP tests, DNS-leak tests, application-level tests
- [ ] One-click connect/disconnect, accurate active-state display

## M3: Distributable Linux package

- [ ] Signed/pinned engine dependencies and source-provenance checks
- [ ] Debian package with reliable installation, upgrade and removal
- [ ] Test on Linux Mint and Ubuntu clean VMs
- [ ] Accessibility, translations, backup/reset UX
- [ ] User docs, release notes, and reproducible release checks

## M4: Broader distribution support

- [ ] Fedora and Arch support once tested in VMs/hardware
- [ ] More sing-box protocols/transports by capability matrix
- [ ] Community issue triage, security response process and release cadence

## Definition of done for first public VPN beta

No manual terminal setup for install or connect; no root GUI; safe disconnect; documented leaks/limits; clean install/uninstall; reproducible tests; realistic support matrix.
