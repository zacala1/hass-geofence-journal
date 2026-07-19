# Geofence Journal

Geofence Journal is a local Home Assistant custom integration that records
confirmed geofence enter and exit events from `person` and `device_tracker`
coordinates. Version 0.1.0 focuses on deterministic location decisions,
restart recovery, and explicit data lifecycle controls.

## Requirements

- Home Assistant 2026.7
- Python 3.14.2 through 3.14.x
- HACS for the recommended installation path

Only a single config entry is supported. Geofence Journal does not provide YAML
configuration.

## Installation

### HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/zacala1/hass-geofence-journal` as an **Integration**.
3. Download **Geofence Journal**, restart Home Assistant, and clear the browser
   cache if Home Assistant does not immediately discover the integration.

### Manual installation

1. Download the release ZIP and extract it at the Home Assistant configuration
   root, or copy `custom_components/geofence_journal` into the
   `custom_components` directory under that root.
2. Restart Home Assistant.

For either method, go to **Settings > Devices & services > Add integration** and
select **Geofence Journal**. The config flow creates the sole config entry and
lets the administrator choose confirmation, cooldown, exit-margin, privacy, and
database settings.

## Deployment readiness

Run release commands from the repository root on Linux with Python 3.14.2
through 3.14.x. The checker fails outside the Git root or when the versions in
`pyproject.toml`, `uv.lock`, `manifest.json`, `const.py`, HACS metadata, README,
or CI runtime declarations disagree:

```bash
uv sync --all-groups --frozen
uv run python -m scripts.release check
uv run python -m scripts.release check v0.1.0
uv run python -m scripts.release build dist
```

The build command creates a deterministic manual-install archive at
`dist/hass-geofence-journal-v0.1.0.zip`. Its paths begin with
`custom_components/geofence_journal`, so extract it at the Home Assistant
configuration root.

Before installing or upgrading, verify all of the following:

1. Home Assistant is in the supported 2026.7 release line.
2. The release runner uses Python 3.14.2 through 3.14.x; Python 3.13 is not a
   supported release environment for Home Assistant 2026.7.
3. The Home Assistant host has writable local storage and sufficient free space
   for the dedicated database, WAL, export files, and SQLite compaction.
4. A current Home Assistant backup exists and includes the configuration
   directory. Export any journal data that must also remain human-readable.
5. The integration health entity is checked after restart before old backups or
   preserved database copies are removed.

Maintainers publish only a tag matching the manifest version exactly, such as
`v0.1.0`. A matching tag runs the locked Linux/Python environment check, root
and version contract, Ruff, BasedPyright, pytest with at least 95% coverage,
HACS, Hassfest, and deterministic ZIP generation before GitHub Release
publication. A manual workflow run performs all verification and packaging but
never publishes a release.

## Administrator setup

Version 0.1.0 has no resource-management frontend. An administrator configures
resources in **Developer Tools > Actions** (formerly Services), in this order:

1. `geofence_journal.upsert_tracker` for a `person` or `device_tracker` entity.
2. `geofence_journal.upsert_place` for coordinates or a live `zone.*` entity.
3. `geofence_journal.upsert_journal` for the event journal.
4. `geofence_journal.upsert_rule` to link the tracker, place, and journal.

Each upsert returns a stable UUID when response data is requested. Supply that
UUID to update an existing resource or link it from the next action. HA Zone
coordinates and radius are read again on each tracker observation, so later zone
edits take effect without rewriting the place.

Administrative actions also support manual events, exclude/restore status
changes, CSV export, explicit purge, compaction, and database reset. Home
Assistant administrator permission is required for these actions.

## Storage and Recorder

The default database is dedicated to this integration at:

```text
.storage/geofence_journal/geofence_journal.db
```

It is SQLite schema v1 with write-ahead logging (WAL) and foreign keys enabled.
Home Assistant Recorder is not authoritative for journal history, and Geofence
Journal does not use Recorder for recovery or permanent retention. Recorder may
still retain the exposed sensor states according to the Home Assistant Recorder
configuration; those records are only a display and automation aid. Keep the
database path inside the Home Assistant configuration tree unless the Home
Assistant process has reliable access to another local path.

## Privacy and retention

Raw coordinates are not stored by default. Geofence decisions still work when
coordinate storage is disabled. If an administrator enables coordinate storage,
exports include coordinates only when explicitly requested and when those values
were originally retained.

Journal data is local and retained indefinitely. There is no automatic retention
purge and no cloud synchronization performed by this integration. Use explicit
purge or reset actions when local policy requires deletion.

## Export

`geofence_journal.export_journal` produces a CSV file with a UTF-8 BOM for
spreadsheet compatibility. The download URL is authenticated, expires after 24
hours, and is not suitable for public sharing. Expired export files are removed.

Treat an export as sensitive even when coordinates are omitted: place, tracker,
and timestamp history can reveal routines. Download it promptly and store or
delete it according to your local policy.

## Purge, compact, and reset

`geofence_journal.purge_events` is deliberately two-stage. Run a dry-run first
to review the number of matching events, then repeat with confirmation to delete
them permanently. Retention is never enforced automatically.

`geofence_journal.compact_database` checkpoints WAL and runs SQLite `VACUUM`.
It can require temporary extra disk space and should be scheduled when event
traffic is low.

`geofence_journal.reset_database` permanently removes all journal and runtime
data. It accepts only this exact phrase:

```text
DELETE ALL GEOFENCE JOURNAL DATA
```

Destructive actions make no automatic backup. Export the affected journal and
create a Home Assistant backup first. Verify that the backup contains the Home
Assistant configuration directory before purging or resetting data.

## Backup and restore

Use a Home Assistant backup as the primary whole-database recovery point and a
CSV export as a portable, human-readable record. Stop Home Assistant before
copying or replacing the live SQLite database so that the database, WAL, and
shared-memory state cannot be captured inconsistently.

To restore, stop Home Assistant, preserve the current database files separately,
restore a backup made by a compatible integration version, and start Home
Assistant. Confirm integration health and the last-event sensor before deleting
the preserved copy. CSV is an export format, not an import or database restore
format in v0.1.0.

## Troubleshooting

- **Integration cannot be added twice:** this is expected; only one config entry
  is allowed.
- **No event appears:** verify that the tracker is enabled, coordinates are valid,
  GPS accuracy meets the rule limit, and the confirmation interval has elapsed.
  The first usable sample establishes a baseline and does not create an event.
- **A zone edit has no immediate event:** zone geometry is evaluated on the next
  tracker observation; a zone-only update does not synthesize a transition.
- **Database is locked or unavailable:** confirm path permissions and free disk
  space, then retry setup. Do not place the database on unreliable network
  storage.
- **Database is corrupt or has a newer schema:** setup fails and leaves the
  original data untouched. The integration will never silently reset or replace
  that database. Preserve the files, inspect the Home Assistant log, restore a
  compatible backup if available, and attach redacted diagnostics when opening
  an issue. Do not delete the database merely to make setup succeed.

Issues are tracked at
<https://github.com/zacala1/hass-geofence-journal/issues>.

## Deferred features

A management UI, map picker, custom panel or card, event editor, stay/commute
analysis, and monthly statistics are intentionally deferred beyond v0.1.0.

## License

MIT. See [LICENSE](LICENSE).
