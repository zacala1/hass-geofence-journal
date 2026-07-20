---
applyTo: "**"
---

# Home Assistant development contract

This is an original first-party Home Assistant custom integration. Do not introduce fork or upstream-sync assumptions.

- Verify Home Assistant and HACS behavior against official documentation and the installed target source before changing compatibility or lifecycle behavior.
- The current supported baseline is Home Assistant 2026.7.x on its required Python 3.14.2+ runtime. For a compatibility-update task, verify the new HA/Python contract first and update every baseline declaration and test matrix atomically; do not treat 2026.7 as permanent.
- Follow PEP 440. Preview versions use `X.Y.ZbN`; use `X.Y.ZrcN` only after feature completion. Tags are immutable and exactly `v<manifest-version>`.
- Keep `pyproject.toml`, `uv.lock`, `manifest.json`, `const.py`, README, release checks, archive name, and GitHub tag synchronized.
- Declare and exactly pin every external runtime requirement in `manifest.json`; prove the release zip works in a clean HA environment.
- Match the archive layout to the verified HACS extraction contract. With the standard `zip_release` flow, place integration runtime contents at the ZIP root because HACS extracts them into `custom_components/<domain>`.
- Keep Config Entry setup, generation refresh, unload, maintenance, SQLite, and backup lifecycles atomic and rollback-safe. Blocking I/O stays off the event loop.
- Preserve privacy defaults: coordinates are not stored unless enabled and never appear on the HA event bus. Purge/reset remain administrator-only and explicitly confirmed.
- Run Ruff, Ruff format, BasedPyright, pytest with branch coverage at least 95%, HACS, Hassfest, deterministic archive checks, and minimum/latest HA smoke tests before release.
- Pin Actions by full SHA, maintain weekly dependency updates, and review HA deprecations before each compatibility change.
- Publish beta/RC tags only as GitHub prereleases, never as “Latest”, and document HACS prerelease-switch activation for testers. Every stable release requires explicit user approval and a fresh full validation run; this task's next stable target is `v0.1.0`.
