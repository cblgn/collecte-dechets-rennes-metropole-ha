"""Entité calendrier : un événement « journée entière » par jour de collecte."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import CollecteConfigEntry, CollecteCoordinator
from .entity import CollecteEntity
from .schedule import Collection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CollecteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([CollecteCalendar(entry.runtime_data)])


class CollecteCalendar(CollecteEntity, CalendarEntity):
    _attr_translation_key = "collections"

    def __init__(self, coordinator: CollecteCoordinator) -> None:
        super().__init__(coordinator, "calendar")
        self._strings: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        # Textes des événements : section « common » des traductions (repli sur l'anglais géré par HA).
        translations = await async_get_translations(self.hass, self.language, "common", {DOMAIN})
        prefix = f"component.{DOMAIN}.common."
        self._strings = {k.removeprefix(prefix): v for k, v in translations.items() if k.startswith(prefix)}
        await super().async_added_to_hass()

    def _text(self, key: str, **placeholders: str) -> str:
        return self._strings.get(key, key).format(**placeholders)

    @property
    def event(self) -> CalendarEvent | None:
        nxt = self.coordinator.data.next_collection(dt_util.now().date())
        return self._to_event(nxt) if nxt else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        start = dt_util.as_local(start_date)
        end = dt_util.as_local(end_date)
        # Un événement « journée entière » le jour J chevauche [start, end[ si J >= start.date() et minuit(J) < end.
        last = end.date() + timedelta(days=1) if end.time() != time.min else end.date()
        collections = self.coordinator.data.collections_between(start.date(), last)
        return [self._to_event(c) for c in collections]

    def _to_event(self, collection: Collection) -> CalendarEvent:
        schedule = self.coordinator.data
        types = [schedule.waste_types[w] for w in collection.waste_types]
        names = [t.name(self.language) for t in types]
        # Libellé du bac par type de déchet (« bac gris »), sinon le nom du déchet.
        bins = " + ".join(self._strings.get(f"bin_{t.key}", n) for t, n in zip(types, names))
        return CalendarEvent(
            start=collection.day,
            end=collection.day + timedelta(days=1),
            summary=self._text("event_summary", bins=bins),
            description=self._text(
                "event_description",
                waste_types=", ".join(names),
                start=schedule.collection_start.strftime("%H:%M"),
            ),
            uid=f"{schedule.zone_id}-{collection.day.isoformat()}",
        )
