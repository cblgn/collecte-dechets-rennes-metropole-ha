"""Tests de bout en bout de l'intégration dans Home Assistant."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from custom_components.collecte_dechets_rennes_metropole.const import (
    CONF_DATA_URL,
    CONF_REMINDER_TIME,
    CONF_ZONE,
    DOMAIN,
)

from .conftest import DATA_FILE

FLOW_MODULE = "custom_components.collecte_dechets_rennes_metropole.config_flow"


async def test_config_flow_default_url(hass: HomeAssistant) -> None:
    with patch(f"{FLOW_MODULE}.DEFAULT_DATA_URL", DATA_FILE):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "zone"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ZONE: "acigne"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acigné"
    assert result["data"] == {CONF_ZONE: "acigne"}
    assert result["options"][CONF_DATA_URL] == DATA_FILE

    # Même zone une seconde fois : refusée
    with patch(f"{FLOW_MODULE}.DEFAULT_DATA_URL", DATA_FILE):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ZONE: "acigne"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_asks_url_when_default_fails(hass: HomeAssistant) -> None:
    with patch(f"{FLOW_MODULE}.DEFAULT_DATA_URL", "/inexistant/collectes.json"):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "source"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DATA_URL: "/inexistant.json"})
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DATA_URL: DATA_FILE})
    assert result["step_id"] == "zone"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Acigné",
        unique_id="acigne",
        data={CONF_ZONE: "acigne"},
        options={CONF_DATA_URL: DATA_FILE, CONF_REMINDER_TIME: "18:00:00"},
    )


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")


async def test_entities(paris, freezer) -> None:
    hass = paris
    # Lundi 5 octobre 2026, 19h à Paris : bac gris le mardi 6 (Acigné)
    freezer.move_to("2026-10-05 17:00:00+00:00")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    om = hass.states.get(_entity_id(hass, entry, "sensor", "ordures_menageres"))
    assert om.state == "2026-10-06"
    assert om.attributes["days_until"] == 1
    assert om.attributes["bin"] == "gris"
    assert hass.states.get(_entity_id(hass, entry, "sensor", "recyclables")).state == "2026-10-16"

    nxt = hass.states.get(_entity_id(hass, entry, "sensor", "next_collection"))
    assert nxt.state == "2026-10-06"
    assert nxt.attributes["waste_types"] == ["ordures_menageres"]

    take_out = _entity_id(hass, entry, "binary_sensor", "take_out_bin")
    assert hass.states.get(take_out).state == "on"
    assert hass.states.get(take_out).attributes["collection_date"] == "2026-10-06"

    # Mardi 6, 6h : la collecte a commencé -> le rappel s'éteint tout seul
    freezer.move_to("2026-10-06 04:00:00+00:00")
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(take_out).state == "off"

    calendar = _entity_id(hass, entry, "calendar", "calendar")
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": calendar, "start_date_time": "2026-12-20 00:00:00", "end_date_time": "2027-01-01 00:00:00"},
        blocking=True,
        return_response=True,
    )
    starts = [e["start"] for e in events[calendar]["events"]]
    assert starts == ["2026-12-26", "2026-12-29"]  # Noël reporté au samedi, puis semaine 53

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"data_outdated_{entry.entry_id}") is None


async def test_outdated_issue(paris, freezer) -> None:
    hass = paris
    freezer.move_to("2027-10-02 10:00:00+00:00")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"data_outdated_{entry.entry_id}") is not None
    assert hass.states.get(_entity_id(hass, entry, "binary_sensor", "take_out_bin")).state == "off"


async def test_uses_cache_when_source_unavailable(paris, freezer) -> None:
    hass = paris
    freezer.move_to("2026-10-05 10:00:00+00:00")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    coordinator.url = "/inexistant.json"
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.zone_id == "acigne"


async def test_options_flow(paris) -> None:
    hass = paris
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_REMINDER_TIME: "20:30:00", CONF_DATA_URL: "/inexistant.json"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_REMINDER_TIME: "20:30:00", CONF_DATA_URL: DATA_FILE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_REMINDER_TIME] == "20:30:00"


async def test_options_reminder_only_works_offline(paris) -> None:
    """Changer seulement l'heure du rappel ne doit pas dépendre du réseau."""
    hass = paris
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.collecte_dechets_rennes_metropole.api import CannotFetchError

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(f"{FLOW_MODULE}.async_fetch_data", side_effect=CannotFetchError("hors ligne")) as fetch:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_REMINDER_TIME: "19:00:00", CONF_DATA_URL: DATA_FILE}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    fetch.assert_not_called()


async def test_calendar_texts_translated(paris, freezer) -> None:
    hass = paris
    freezer.move_to("2026-10-05 10:00:00+00:00")
    hass.config.language = "fr"
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    calendar = _entity_id(hass, entry, "calendar", "calendar")
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": calendar, "start_date_time": "2026-10-06 00:00:00", "end_date_time": "2026-10-07 00:00:00"},
        blocking=True,
        return_response=True,
    )
    event = events[calendar]["events"][0]
    assert event["summary"] == "Collecte bac gris"
    assert event["description"] == "Ordures ménagères. Sortir le bac la veille au soir (passage dès 05:30)."


async def test_zone_missing_issue_and_small_cache(paris, freezer, tmp_path) -> None:
    import json

    hass = paris
    freezer.move_to("2026-10-05 10:00:00+00:00")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    # Le cache ne garde que la zone configurée
    assert list(coordinator._cached["zones"]) == ["acigne"]

    raw = json.loads(open(DATA_FILE, encoding="utf-8").read())
    del raw["zones"]["acigne"]
    other = tmp_path / "collectes.json"
    other.write_text(json.dumps(raw), encoding="utf-8")
    coordinator.url = str(other)
    await coordinator.async_refresh()

    issue_id = f"zone_missing_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert coordinator.last_update_success
    assert coordinator.data.zone_id == "acigne"  # dernier calendrier connu

    coordinator.url = DATA_FILE
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_implausible_update_rejected(paris, freezer, tmp_path) -> None:
    import json

    hass = paris
    freezer.move_to("2026-10-05 10:00:00+00:00")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    issue_id = f"data_rejected_{entry.entry_id}"

    # Fichier tronqué : il ne reste que 3 zones sur 53
    raw = json.loads(open(DATA_FILE, encoding="utf-8").read())
    raw["zones"] = {k: v for k, v in raw["zones"].items() if k in ("acigne", "betton", "bruz")}
    bad = tmp_path / "collectes.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    coordinator.url = str(bad)
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert coordinator.last_update_success
    assert coordinator._cached["zone_count"] == 53  # le cache n'a pas été écrasé

    coordinator.url = DATA_FILE
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
