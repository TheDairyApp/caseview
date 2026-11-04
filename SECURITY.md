# Security policy

## Supported versions

Security fixes go into the latest release. Please update before reporting.

## Reporting a vulnerability

**Please don't open a public issue for security problems.** Use GitHub's [private vulnerability reporting](../../security/advisories/new) for this repository instead.

Include:
- what is affected (e.g. phone access server, autostart task, display driver)
- steps to reproduce, and the impact you expect
- your CaseView and Windows versions

You'll get an acknowledgement within a few days. Fixes are released as soon as practical, and credited unless you'd rather stay anonymous.

## Security-relevant design

- **Administrator rights:** sensors need them. With *Start with Windows*, CaseView runs from a Task Scheduler logon task with highest privileges. It only reads sensors and writes to displays and lighting. It never changes fan, clock, voltage or power settings.
- **Phone access:** off by default. When on, it listens on the local network on one port, serves only private and loopback addresses, requires a 6-digit PIN on every API call (compared in constant time), locks an address out after 5 wrong PINs, and limits request bodies to 4 KB. It uses plain HTTP: don't expose it to the internet.
- **OpenRGB:** CaseView connects to the OpenRGB SDK server on `127.0.0.1` only.
- **Kernel driver:** CaseView ships no driver. It relies on [PawnIO](https://pawnio.eu) (signed), installed separately by the user.
- **Downloads:** `tools/fetch_lhm.py` fetches pinned package versions from nuget.org and prints their SHA-256.
