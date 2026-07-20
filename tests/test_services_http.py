from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from importlib.util import find_spec
from threading import get_ident
from typing import TYPE_CHECKING, Final, final

import pytest
from custom_components.geofence_journal import http
from custom_components.geofence_journal.export import ExportDownload, ExportRegistry
from custom_components.geofence_journal.http import (
    async_cleanup_orphaned_exports,
    async_register_export_view,
    async_schedule_export_cleanup,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from homeassistant.core import HomeAssistant

    class _Headers(Protocol):
        def __getitem__(self, key: str) -> str: ...

    class _Response(Protocol):
        status: int
        headers: _Headers

        async def read(self) -> bytes: ...

    class _Client(Protocol):
        async def get(self, path: str) -> _Response: ...

    class _ClientSessionGenerator(Protocol):
        async def __call__(self, access_token: str | None = None) -> _Client: ...


NOT_APP_KEY_WARNING: Final = "aiohttp.web_exceptions.NotAppKeyWarning"
pytestmark: Final = [
    pytest.mark.filterwarnings(
        "ignore:It is recommended to use web.AppKey instances:" + NOT_APP_KEY_WARNING
    ),
    pytest.mark.filterwarnings(
        "ignore:It is recommended to use web.RequestKey instances:"
        + NOT_APP_KEY_WARNING
    ),
]


@final
class HttpClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 18, 12, tzinfo=UTC)

    def utc_now(self) -> datetime:
        return self._now

    def expire(self) -> None:
        self._now += timedelta(hours=24)


async def _register_view(hass: HomeAssistant, registry: ExportRegistry) -> None:
    assert await async_setup_component(hass, "http", {})
    async_register_export_view(hass, registry)


def test_export_http_module_exists() -> None:
    # Given: exports require an authenticated HA-native download route.
    module_name = "custom_components.geofence_journal.http"

    # When: the HTTP adapter is discovered.
    http_spec = find_spec(module_name)

    # Then: no frontend or unauthenticated static directory is needed.
    assert http_spec is not None


def test_export_http_module_exposes_scheduled_cleanup() -> None:
    # Given: file expiry must not depend on a later download attempt.

    # When: the public HTTP lifecycle contract is inspected.
    exposed_names = set(dir(http))

    # Then: Task 5 can schedule deletion at artifact creation time.
    assert "async_schedule_export_cleanup" in exposed_names


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_export_download_rejects_unauthenticated_and_returns_bom_to_admin(
    hass: HomeAssistant,
    hass_client: _ClientSessionGenerator,
    hass_client_no_auth: _ClientSessionGenerator,
    tmp_path: Path,
) -> None:
    # Given: a registered view and one generated UTF-8 BOM artifact.
    registry = ExportRegistry(tmp_path / "exports", HttpClock())
    artifact = registry.allocate()
    _ = artifact.path.write_bytes(b"\xef\xbb\xbfevent_id\r\n")
    await _register_view(hass, registry)
    unauthenticated = await hass_client_no_auth()
    admin = await hass_client()

    # When: the same opaque URL is requested without and with HA auth.
    rejected = await unauthenticated.get(artifact.url)
    accepted = await admin.get(artifact.url)

    # Then: auth is mandatory and the admin receives the exact CSV bytes.
    assert rejected.status == HTTPStatus.UNAUTHORIZED
    assert accepted.status == HTTPStatus.OK
    assert await accepted.read() == b"\xef\xbb\xbfevent_id\r\n"
    assert accepted.headers["Content-Disposition"].startswith("attachment;")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_export_download_requires_admin(
    hass: HomeAssistant,
    hass_client: _ClientSessionGenerator,
    hass_read_only_access_token: str,
    tmp_path: Path,
) -> None:
    # Given: an authenticated non-admin and a valid artifact.
    registry = ExportRegistry(tmp_path / "exports", HttpClock())
    artifact = registry.allocate()
    _ = artifact.path.write_bytes(b"csv")
    await _register_view(hass, registry)
    read_only = await hass_client(hass_read_only_access_token)

    # When: the non-admin requests the download.
    response = await read_only.get(artifact.url)

    # Then: authentication alone does not cross the admin boundary.
    assert response.status == HTTPStatus.UNAUTHORIZED


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_export_download_rejects_symlinked_artifact(
    hass: HomeAssistant,
    hass_client: _ClientSessionGenerator,
    tmp_path: Path,
) -> None:
    registry = ExportRegistry(tmp_path / "exports", HttpClock())
    artifact = registry.allocate()
    secret = tmp_path / "private.txt"
    _ = secret.write_bytes(b"must-not-be-served")
    artifact.path.symlink_to(secret)
    await _register_view(hass, registry)
    admin = await hass_client()

    response = await admin.get(artifact.url)

    assert response.status == HTTPStatus.NOT_FOUND
    assert secret.read_bytes() == b"must-not-be-served"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_export_download_streams_verified_file_after_path_swap(
    hass: HomeAssistant,
    hass_client: _ClientSessionGenerator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ExportRegistry(tmp_path / "exports", HttpClock())
    artifact = registry.allocate()
    expected = b"verified-export"
    _ = artifact.path.write_bytes(expected)
    secret = tmp_path / "private.txt"
    _ = secret.write_bytes(b"must-not-be-served")
    original_open = ExportRegistry.open_download

    def swap_path_after_open(
        self: ExportRegistry, export_id: str
    ) -> ExportDownload | None:
        download = original_open(self, export_id)
        assert download is not None
        try:
            artifact.path.unlink()
            artifact.path.symlink_to(secret)
        except OSError as error:
            download.stream.close()
            pytest.skip(f"platform cannot replace an open export path: {error}")
        return download

    monkeypatch.setattr(ExportRegistry, "open_download", swap_path_after_open)
    await _register_view(hass, registry)
    admin = await hass_client()

    response = await admin.get(artifact.url)

    assert response.status == HTTPStatus.OK
    assert await response.read() == expected
    assert secret.read_bytes() == b"must-not-be-served"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_export_download_removes_expired_or_stale_artifact(
    hass: HomeAssistant,
    hass_client: _ClientSessionGenerator,
    tmp_path: Path,
) -> None:
    # Given: a file whose deterministic 24-hour lifetime has elapsed.
    clock = HttpClock()
    registry = ExportRegistry(tmp_path / "exports", clock)
    artifact = registry.allocate()
    _ = artifact.path.write_bytes(b"csv")
    await _register_view(hass, registry)
    clock.expire()
    admin = await hass_client()

    # When: the expired URL and a traversal-shaped opaque ID are requested.
    expired = await admin.get(artifact.url)
    traversal = await admin.get(
        "/api/geofence_journal/export/%2e%2e%2fgeofence_journal.db"
    )

    # Then: both are unavailable and expiry cleanup removes the file.
    assert expired.status == HTTPStatus.NOT_FOUND
    assert traversal.status == HTTPStatus.BAD_REQUEST
    assert not artifact.path.exists()


async def test_export_file_is_deleted_on_schedule_without_a_download(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    # Given: a generated artifact whose cleanup timer is attached at creation.
    registry = ExportRegistry(tmp_path / "exports", HttpClock())
    artifact = registry.allocate()
    _ = artifact.path.write_bytes(b"csv")
    cancel = async_schedule_export_cleanup(hass, registry, artifact)

    # When: HA time advances beyond 24 hours without any HTTP request.
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(hours=25))
    await hass.async_block_till_done()

    # Then: both registry entry and file are removed automatically.
    assert registry.resolve(artifact.export_id) is None
    assert not artifact.path.exists()
    cancel()


async def test_restart_cleanup_removes_orphans_off_the_event_loop(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a restart leaves unregistered CSV and temporary export files behind.
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    orphan_csv = exports_dir / "old.csv"
    orphan_tmp = exports_dir / "interrupted.tmp"
    unrelated = exports_dir / "keep.txt"
    _ = orphan_csv.write_bytes(b"csv")
    _ = orphan_tmp.write_bytes(b"partial")
    _ = unrelated.write_bytes(b"keep")
    registry = ExportRegistry(exports_dir, HttpClock())
    event_thread = get_ident()
    worker_threads: list[int] = []
    cleanup = ExportRegistry.cleanup_orphaned_files

    def tracked_cleanup(self: ExportRegistry) -> int:
        worker_threads.append(get_ident())
        return cleanup(self)

    monkeypatch.setattr(ExportRegistry, "cleanup_orphaned_files", tracked_cleanup)

    # When: integration setup cleans files whose URLs cannot survive restart.
    removed = await async_cleanup_orphaned_exports(hass, registry)

    # Then: disk I/O ran in a worker and only export artifacts were removed.
    assert removed == 2
    assert len(worker_threads) == 1
    assert worker_threads[0] != event_thread
    assert not orphan_csv.exists()
    assert not orphan_tmp.exists()
    assert unrelated.read_bytes() == b"keep"
