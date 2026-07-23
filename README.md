# Geofence Journal

<p align="center">
  <img src="custom_components/geofence_journal/brand/icon.png"
       alt="Geofence Journal" width="112">
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=zacala1&repository=hass-geofence-journal&category=integration">
    <img src="https://my.home-assistant.io/badges/hacs_repository.svg"
         alt="Open this repository in HACS">
  </a>
</p>

Geofence Journal is a local Home Assistant custom integration that records
confirmed geofence enter and exit events from `person` and `device_tracker`
coordinates. Version 0.1.0b2 is a user-test beta focused on deterministic
location decisions, restart recovery, bounded runtime work, privacy, and
explicit data lifecycle controls.

This build is a GitHub prerelease. Stable `v0.1.0` has not been published and
will be created only after beta feedback and a fresh validation run.

The integration uses a dedicated, local SQLite database as the authoritative
journal. It supports hysteresis, GPS accuracy filtering, confirmation windows,
cooldowns, restart-safe pending state, duplicate replay protection,
authenticated CSV export, administrator-only management actions, private
diagnostics, and Home Assistant Repairs notifications. It does not send journal
data to a cloud service.

## Requirements

- Home Assistant 2026.7
- Python 3.14.2 through 3.14.x
- HACS for the recommended installation path

Only a single config entry is supported. Geofence Journal does not provide YAML
configuration.

## Installation

### HACS

1. Open this repository with the HACS badge above, or open **Custom
   repositories** in HACS and add
   `https://github.com/zacala1/hass-geofence-journal` as an **Integration**.
2. Enable the repository's **HACS prerelease** switch so HACS can offer beta
   versions, then select `v0.1.0b2`.
3. Download **Geofence Journal** and restart Home Assistant.
4. If Home Assistant does not immediately discover the integration, clear the
   browser cache and retry.

### Manual installation

1. Download `geofence_journal.zip` from the `v0.1.0b2` GitHub prerelease.
2. Create `custom_components/geofence_journal` below the Home Assistant
   configuration directory.
3. Extract the ZIP contents directly into that directory. The ZIP is rooted at
   the integration contents, not at `custom_components`.
4. Restart Home Assistant.

For either method, go to **Settings > Devices & services > Add integration** and
select **Geofence Journal**. The config flow creates the sole config entry and
lets the administrator choose confirmation, cooldown, exit-margin, privacy, and
database settings.

## Deployment readiness

Run release commands from the repository root on Linux with Python 3.14.2
through 3.14.x. The checker fails outside the Git root or when versions in
`pyproject.toml`, `uv.lock`, `manifest.json`, `const.py`, HACS metadata, README,
or CI runtime declarations disagree:

```bash
uv sync --all-groups --frozen
uv run python -m scripts.release check
uv run python -m scripts.release check v0.1.0b2
uv run python -m scripts.release build dist
```

The build command creates the deterministic HACS/manual-install archive
`dist/geofence_journal.zip`. Its root contains `manifest.json`, Python modules,
local brand icons, action metadata, and translations, so extract it into
`custom_components/geofence_journal`.

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
`v0.1.0b2`. A matching tag runs the locked environment and repository contract,
Ruff, BasedPyright, pytest with at least 95% branch coverage, HACS, Hassfest,
minimum/latest HA smoke tests, and deterministic ZIP generation before GitHub
Release publication. Beta and release-candidate tags are GitHub prereleases and
never Latest. A manual workflow run verifies and packages but never publishes.

## Administrator setup

Version 0.1.0b2 has no resource-management frontend. Configure resources in
**Developer Tools > Actions** (formerly Services), in this order:

1. `geofence_journal.upsert_tracker` for a `person` or `device_tracker` entity.
2. `geofence_journal.upsert_place` for coordinates or a live `zone.*` entity.
3. `geofence_journal.upsert_journal` for the event journal.
4. `geofence_journal.upsert_rule` to link the tracker, place, and journal.

Each upsert returns a stable UUID when response data is requested. Supply that
UUID to update an existing resource or link it from the next action. HA Zone
coordinates and radius are read again on each tracker observation, so later zone
edits take effect without rewriting the place.

Use `geofence_journal.list_resources` to discover current UUIDs and
configuration, or `geofence_journal.get_resource` for one exact resource.
`geofence_journal.delete_resource` requires `confirm: true` and refuses to delete
a tracker, place, or journal that is still referenced by a rule. Delete the
dependent rule first.

The complete administrator action surface is:

| Purpose | Actions |
| --- | --- |
| Configure | `upsert_tracker`, `upsert_place`, `upsert_journal`, `upsert_rule` |
| Discover and delete | `list_resources`, `get_resource`, `delete_resource` |
| Journal events | `add_event`, `exclude_event`, `restore_event` |
| Data lifecycle | `export_journal`, `purge_events`, `purge_retention`, `compact_database`, `reset_database` |

All these actions require a Home Assistant administrator. Event creation and
status changes retain an audit revision when applicable.

## Defaults

| Setting | Default |
| --- | --- |
| Store raw coordinates | Off |
| Enter confirmation | 120 seconds |
| Exit confirmation | 180 seconds |
| Duplicate-event cooldown | 300 seconds |
| Exit hysteresis margin | 50 meters |
| Rule GPS accuracy limit | 200 meters |
| Journal `retention_days` | Unset; records are retained indefinitely |
| Automatic retention purge | Off |
| Database path | `.storage/geofence_journal/geofence_journal.db` |

Per-place and per-rule values can override the config-entry timing and margin
defaults. Updating config resources briefly drains accepted observations and
rebuilds the active listener generation.

## Entities and automation

- `sensor.geofence_journal_last_event` reports the UTC timestamp of the latest
  committed automatic or manual event.
- `binary_sensor.geofence_journal_healthy` reports whether the dedicated
  database is available.
- The `geofence_journal_event` event is fired after a committed event change. It
  includes stable IDs, names, event type, status, and timestamp, but never raw
  coordinates or GPS accuracy.

## Storage and Recorder

The default database is dedicated to this integration at:

```text
.storage/geofence_journal/geofence_journal.db
```

It is SQLite schema v1 with write-ahead logging (WAL), foreign keys, additive
query indexes, and UTC timestamps. Home Assistant Recorder is not authoritative
for journal history, and Geofence Journal does not use Recorder for recovery or
permanent retention. Recorder may retain exposed sensor states according to the
Home Assistant Recorder configuration; those records are only a display and
automation aid.

Keep the database path inside the Home Assistant configuration tree unless the
Home Assistant process has reliable access to another local path.

The dedicated database stores:

| Data | Stored fields |
| --- | --- |
| Trackers | Entity ID, tracker kind, display name, enabled state |
| Places | Name, fixed geometry or `zone.*` reference, radius, exit margin, enabled state |
| Journals and rules | Names, links, confirmation windows, cooldown, accuracy limit, optional `retention_days` |
| Events | Type, timestamps, source, status, note, linked resource IDs, and coordinates only when enabled |
| Revisions | Exclude/restore audit action, timestamp, optional reason and acting HA user ID |
| Runtime state | Baseline presence, pending transition/deadline, cooldown, and last-event identity |

## Privacy and retention

Raw coordinates are not stored by default. Geofence decisions still work when
coordinate storage is disabled. If an administrator enables coordinate storage,
exports include coordinates only when explicitly requested and when those
values were originally retained. The Home Assistant event bus and private
diagnostics never include raw coordinates.

Journal data is local and retained indefinitely when `retention_days` is unset.
There is no automatic retention purge and no cloud synchronization. A configured
`retention_days` value supplies the cutoff only when an administrator explicitly
calls `geofence_journal.purge_retention`.

## Export

`geofence_journal.export_journal` produces a UTF-8 BOM CSV for spreadsheet
compatibility. Export reads use an independent SQLite snapshot, so a long export
does not hold the serialized runtime writer. The download URL is authenticated,
expires after 24 hours, and is not suitable for public sharing. Expired export
files are removed.

Coordinates are omitted unless the caller explicitly requests them and
coordinate storage was enabled when the event was written. Treat every export
as sensitive: place, tracker, and timestamp history can reveal routines.

## Purge, compact, and reset

`geofence_journal.purge_events` is deliberately two-stage. Run a dry-run first
to review matching event and revision counts, then repeat with `dry_run: false`
and `confirm: true` to delete them permanently.

`geofence_journal.purge_retention` uses the selected journal's configured
retention period and has the same dry-run and confirmation controls. Retention
is never enforced automatically.

`geofence_journal.compact_database` checkpoints WAL and runs SQLite `VACUUM`.
Schedule it when event traffic is low. Export and compaction perform a
conservative free-space preflight and fail before mutation when the target
filesystem lacks headroom.

`geofence_journal.reset_database` permanently removes all resources, events,
revisions, and runtime state. It accepts only this exact phrase:

```text
DELETE ALL GEOFENCE JOURNAL DATA
```

Purge and reset are unrecoverable. Destructive actions make no automatic backup.
Export the affected journal and create a Home Assistant backup first. Verify
that the backup contains the Home Assistant configuration directory before
deleting data.

## Diagnostics and Repairs

Download diagnostics from the Geofence Journal config entry when
troubleshooting. Diagnostics expose schema and health invariants, resource/event
counts, and file sizes; they omit database paths, resource identities,
coordinates, notes, user IDs, and event timestamps. Treat even these private
diagnostics as sensitive.

When the runtime detects a database failure,
`binary_sensor.geofence_journal_healthy` turns off and Home Assistant creates a
non-fixable Repairs issue. The issue is removed after storage health recovers.

## Operational guardrails

Public action inputs are bounded to prevent accidental unbounded rows or timers:
names are limited to 128 characters, event notes to 4096, revision reasons to
512, confirmation windows to one day, cooldowns to seven days, and configured
retention to 36,500 days.

Runtime observations retain at most the latest pending state per tracker while
one state is being processed, so a burst cannot grow an unbounded in-memory
queue. Missing, malformed, inaccurate, or out-of-order coordinates do not change
presence state.

## Backup and restore

Use a Home Assistant backup as the primary whole-database recovery point and a
CSV export as a portable, human-readable record. Before a Home Assistant backup,
the integration pauses observations, drains accepted writes, and closes SQLite;
afterward it reopens the database and restores the listener generation. The
default database is below the Home Assistant configuration directory and is
included in the standard configuration backup.

An absolute database path outside the Home Assistant configuration directory is
not included in the standard Home Assistant backup archive. Back it up and
restore it separately with Home Assistant stopped. Also stop Home Assistant
before manually copying or replacing any live SQLite database so the database,
WAL, and shared-memory state cannot be captured inconsistently.

To restore, stop Home Assistant, preserve the current database files separately,
restore a backup made by a compatible integration version, and start Home
Assistant. Confirm integration health and the last-event sensor before deleting
the preserved copy. CSV is an export format, not an import or database restore
format in v0.1.0b2.

## Troubleshooting

- **Integration cannot be added twice:** this is expected; only one config entry
  is allowed.
- **No event appears:** verify that the tracker and linked resources are enabled,
  coordinates are valid, GPS accuracy meets the rule limit, and the confirmation
  interval has elapsed. The first usable sample establishes a baseline and does
  not create an event.
- **A zone edit has no immediate event:** zone geometry is evaluated on the next
  tracker observation; a zone-only update does not synthesize a transition.
- **Database is locked or unavailable:** confirm path permissions and free disk
  space, then retry setup. Do not place the database on unreliable network
  storage.
- **An export or compaction reports insufficient space:** free at least the
  reported number of bytes on the target filesystem, then retry. A failed export
  URL is invalidated automatically.
- **The health binary sensor is off:** review the Repairs issue, download
  redacted diagnostics, and inspect the Home Assistant log before restarting or
  restoring data.
- **Database is corrupt or has a newer schema:** setup fails and leaves the
  original data untouched. The integration will never silently reset or replace
  that database. Preserve the files, inspect the Home Assistant log, restore a
  compatible backup if available, and attach redacted diagnostics when opening
  an issue.

For suspected vulnerabilities or privacy failures, do not open a public issue;
follow [SECURITY.md](SECURITY.md). Other issues are tracked at
<https://github.com/zacala1/hass-geofence-journal/issues>.

## Deferred features

A management UI, map picker, custom panel or card, event editor, stay/commute
analysis, and monthly statistics are intentionally deferred beyond stable
v0.1.0.

## License

MIT. See [LICENSE](LICENSE).
