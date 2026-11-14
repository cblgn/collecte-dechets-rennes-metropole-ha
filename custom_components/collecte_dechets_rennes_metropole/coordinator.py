"""Coordinateur : télécharge le JSON, le met en cache et signale les données périmées."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotFetchError, async_fetch_data
from .const import CONF_DATA_URL, CONF_ZONE, DEFAULT_DATA_URL, DOMAIN, UPDATE_INTERVAL
from .schedule import InvalidDataError, ZoneSchedule, parse_schedule

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type CollecteConfigEntry = ConfigEntry[CollecteCoordinator]


def outdated_issue_id(entry_id: str) -> str:
    return f"data_outdated_{entry_id}"


def zone_missing_issue_id(entry_id: str) -> str:
    return f"zone_missing_{entry_id}"


def zone_subset(raw: dict, zone_id: str) -> dict:
    """Ne garde du JSON que ce qui sert à la zone (le fichier complet couvre toutes les zones)."""
    return {**raw, "zones": {zone_id: raw["zones"][zone_id]}}


class CollecteCoordinator(DataUpdateCoordinator[ZoneSchedule]):
    """Fournit le calendrier de la zone configurée."""

    config_entry: CollecteConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CollecteConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_ZONE]}",
            update_interval=UPDATE_INTERVAL,
        )
        self.zone_id: str = entry.data[CONF_ZONE]
        self.url: str = entry.options.get(CONF_DATA_URL, DEFAULT_DATA_URL)
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._cached: dict | None = None
        self._cache_loaded = False

    async def _async_update_data(self) -> ZoneSchedule:
        try:
            raw = await async_fetch_data(self.hass, self.url)
            self._update_zone_missing_issue(self.zone_id in raw.get("zones", {}))
            schedule = parse_schedule(raw, self.zone_id)
        except (CannotFetchError, InvalidDataError) as err:
            schedule = await self._async_load_cache(err)
        else:
            await self._async_save_cache(zone_subset(raw, self.zone_id))

        self._update_outdated_issue(schedule)
        return schedule

    async def _async_get_cache(self) -> dict | None:
        if not self._cache_loaded:
            self._cached = await self._store.async_load()
            self._cache_loaded = True
        return self._cached

    async def _async_save_cache(self, data: dict) -> None:
        """N'écrit sur disque que si les données ont changé."""
        if data != await self._async_get_cache():
            await self._store.async_save(data)
            self._cached = data

    async def _async_load_cache(self, err: Exception) -> ZoneSchedule:
        cached = await self._async_get_cache()
        if cached is None:
            raise UpdateFailed(f"Données indisponibles ({self.url}) : {err}") from err
        try:
            schedule = parse_schedule(cached, self.zone_id)
        except InvalidDataError as cache_err:
            raise UpdateFailed(f"Données indisponibles et cache invalide : {cache_err}") from err
        _LOGGER.warning("Impossible de mettre à jour les collectes (%s), utilisation du cache local", err)
        return schedule

    def _update_zone_missing_issue(self, present: bool) -> None:
        issue_id = zone_missing_issue_id(self.config_entry.entry_id)
        if present:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="zone_missing",
            translation_placeholders={"zone": self.config_entry.title, "url": self.url},
        )

    def _update_outdated_issue(self, schedule: ZoneSchedule) -> None:
        issue_id = outdated_issue_id(self.config_entry.entry_id)
        if schedule.is_outdated(dt_util.now().date()):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="data_outdated",
                translation_placeholders={
                    "zone": schedule.name,
                    "valid_until": schedule.valid_until.isoformat() if schedule.valid_until else "?",
                    "url": self.url,
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
