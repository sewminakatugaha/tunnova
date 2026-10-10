# Contributing to Tunnova

Thanks for helping make Linux VPN connections simpler.

1. Discuss security-impacting changes (TUN, root helpers, DNS, Polkit) in an issue before writing code.
2. Keep PRs focused, include tests and describe expected/actual behavior.
3. Run `python3 -m unittest discover -s tests -v` before submitting.
4. Never share live proxy/VLESS URIs, UUIDs, tokens, server subscription links, credentials, or unredacted logs.
5. Avoid distribution-specific assumptions without testing; document supported environments explicitly.
6. Treat recovery on shutdown and clean package removal as critical product behavior.

Style: readable Python with type hints where useful, stdlib first, no new dependencies without rationale. `git` history should be reviewable; use feature branches and pull requests.
