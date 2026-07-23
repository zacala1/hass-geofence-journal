# Security policy

## Supported releases

Security fixes are provided for the latest published Geofence Journal preview
or stable release. Older previews are not maintained; reproduce the issue on
the newest release before reporting it when that can be done safely.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/zacala1/hass-geofence-journal/security/advisories/new)
so the report and follow-up remain private until a coordinated disclosure is
ready.

Include the Geofence Journal version, Home Assistant version, installation
method, affected operation, expected impact, and the smallest safe reproduction
you can provide. Do not attach a live database, unredacted diagnostics, precise
coordinates, tracker or person names, access tokens, backup archives, or CSV
exports. Synthetic data is preferred.

The maintainer will acknowledge a report as soon as practical, validate its
scope, and coordinate remediation and disclosure with the reporter. Please do
not publish exploit details before a fixed release is available.

## Privacy incidents

Unexpected coordinate retention, unauthenticated export access, disclosure of
location history, or destructive-action authorization bypasses should be
treated as security issues and reported through the same private channel.
