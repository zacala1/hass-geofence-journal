# Changelog

All notable changes to Geofence Journal previews are recorded here. Stable
`v0.1.0` has not yet been published.

## 0.1.0b2

- Stream CSV exports from independent SQLite snapshots so long downloads do not
  block location-event writes.
- Bound observation backlog to the latest state per tracker and add
  event-history indexes for predictable query and purge performance.
- Add resource discovery, exact lookup, and explicitly confirmed safe deletion.
- Add privacy-redacted diagnostics, database-health Repairs issues, and a fixed
  health binary sensor.
- Bound public action inputs and add disk-headroom checks for export and
  compaction.
- Add optional journal retention configuration and an explicit, two-stage
  retention purge; automatic deletion remains disabled.
- Ship a local Home Assistant brand icon, remove the redundant Pydantic manifest
  pin, monitor uv dependencies, and add private vulnerability reporting.

## 0.1.0b1

- Initial user-test preview with dedicated SQLite schema v1, geofence
  enter/exit confirmation, cooldown, restart recovery, coordinate-private event
  bus payloads, CSV export, purge, compaction, reset, and backup lifecycle hooks.
