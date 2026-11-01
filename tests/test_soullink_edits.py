"""Manuelle Korrekturen im Server: Sperren, Encounter-/Link-/Tod-Edits, Rechte."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.arceus import Arceus


HOST = "host1"


def _make_arceus() -> Arceus:
    rem = {"client_port": "43886", "helper_port": "43887", "client_id": HOST}
    a = Arceus("localhost", 43886, rem)
    a.munchlaxes = {HOST: None, "c2": None}
    a.soullink_config = {
        "mode": "coop", "player_count": 2, "link_strategy": "full_chain",
        "team_membership": {}, "expected_owners": ["Alice", "Bob"],
    }
    # Host spielt Slot 1, c2 spielt Slot 2
    a.declared_player_ids_by_client = {HOST: {1}, "c2": {2}}
    a.client_player_ids = {HOST: {1}, "c2": {2}}
    a.sent = []

    async def fake_send(client_id, message):
        a.sent.append((client_id, message))

    a.send_to_client = fake_send
    return a


def enc(pv, owner, route=5, dex=25, outcome="caught"):
    return {
        "personality": pv, "owner": owner, "edition": 51, "route": route,
        "dexnr": dex, "lvl": 5, "shiny": 0, "is_first": 1, "outcome": outcome,
        "method": "wild", "manual_fields": "", "deleted": 0,
    }


def _seed(a: Arceus) -> dict:
    """Alice_1 + Bob_2 auf Route 5 → eine Gruppe."""
    for e in (enc(100, "Alice_1"), enc(200, "Bob_2", dex=12)):
        a.encounters[(e["personality"], e["owner"])] = e
        link = a._assign_link_group(e)
    return link


def run(a: Arceus, fn, *args):
    async def inner():
        fn(*args)
        # Broadcast-Tasks laufen lassen
        for _ in range(5):
            await asyncio.sleep(0)
    asyncio.run(inner())


def rejected(a: Arceus) -> list[dict]:
    return [m for _, m in a.sent if m.get("type") == "edit_rejected"]


def test_encounter_edit_sperrt_und_broadcastet_an_alle():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"outcome": "not_caught"}})
    e = a.encounters[(200, "Bob_2")]
    assert e["outcome"] == "not_caught"
    assert "outcome" in e["manual_fields"]
    targets = {cid for cid, m in a.sent if m.get("type") == "encounter_sync"}
    assert targets == {HOST, "c2"}
    link = next(iter(a.soullink_links.values()))
    assert link["members"]["Bob"]["outcome"] == "not_caught"
    assert link["state"] == "failed"


def test_fremder_encounter_wird_abgelehnt():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 100, "owner": "Alice_1",
        "changes": {"outcome": "not_caught"}})
    assert a.encounters[(100, "Alice_1")]["outcome"] == "caught"
    assert rejected(a) and rejected(a)[0]["request_type"] == "encounter_edit"


def test_host_darf_fremden_encounter_aendern():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_encounter_edit, HOST, {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"lvl": 9}})
    assert a.encounters[(200, "Bob_2")]["lvl"] == 9
    assert not rejected(a)


def test_auto_outcome_ignoriert_gesperrtes_feld():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"outcome": "caught"}})
    a.sent.clear()
    run(a, a._handle_encounter_outcome, "c2", {
        "type": "encounter_outcome", "personality": 200, "owner": "Bob_2",
        "outcome": "not_caught"})
    assert a.encounters[(200, "Bob_2")]["outcome"] == "caught"
    # Sender bekommt Korrektur, kein encounter_outcome-Broadcast
    assert [m["type"] for _, m in a.sent] == ["encounter_sync"]


def test_unlock_laesst_automatik_wieder_zu():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"outcome": "caught"}})
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "unlock": ["outcome"]})
    run(a, a._handle_encounter_outcome, "c2", {
        "type": "encounter_outcome", "personality": 200, "owner": "Bob_2",
        "outcome": "not_caught"})
    assert a.encounters[(200, "Bob_2")]["outcome"] == "not_caught"


def test_loeschen_entfernt_aus_gruppe_und_blockt_resync():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "delete": True})
    link = next(iter(a.soullink_links.values()))
    assert "Bob" not in link["members"]
    # Auto-Sync derselben Zeile darf deleted nicht zuruecksetzen
    e = a.encounters[(200, "Bob_2")]
    assert e["deleted"] == 1
    assert a._assign_link_group(e) is None


def test_encounter_anlegen():
    a = _make_arceus()
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": -5, "owner": "Bob_2",
        "create": {"edition": 51, "route": 7, "dexnr": 1, "outcome": "caught"}})
    e = a.encounters[(-5, "Bob_2")]
    assert e["route"] == 7 and e["deleted"] == 0
    assert "route" in e["manual_fields"]
    # doppelt anlegen → abgelehnt
    run(a, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": -5, "owner": "Bob_2",
        "create": {"edition": 51}})
    assert rejected(a)


def test_link_unlink_sperrt_gegen_neuzuordnung():
    a = _make_arceus()
    link = _seed(a)
    run(a, a._handle_link_edit, "c2", {
        "type": "soullink_link_edit", "op": "unlink",
        "personality": 200, "owner": "Bob_2"})
    assert "Bob" not in link["members"]
    e = a.encounters[(200, "Bob_2")]
    assert "link" in e["manual_fields"]
    assert a._assign_link_group(e) is None


def test_link_move_in_andere_gruppe():
    a = _make_arceus()
    link1 = _seed(a)
    for e in (enc(101, "Alice_1", route=6), enc(201, "Bob_2", route=6)):
        a.encounters[(e["personality"], e["owner"])] = e
    a._assign_link_group(a.encounters[(101, "Alice_1")])
    link2 = a._find_member_link(101, "Alice")
    # Bob_2/201 haengt noch nicht in link2 → Zielgruppe ohne Bob
    run(a, a._handle_link_edit, "c2", {
        "type": "soullink_link_edit", "op": "move", "personality": 200,
        "owner": "Bob_2", "target_link_id": link2["link_id"]})
    assert "Bob" not in link1["members"]
    assert link2["members"]["Bob"]["personality"] == 200
    assert link2["members"]["Bob"]["manual"] == 1


def test_link_create_und_dissolve_nur_host():
    a = _make_arceus()
    _seed(a)
    req = {"type": "soullink_link_edit", "op": "create", "members": [
        {"personality": 100, "owner": "Alice_1"},
        {"personality": 200, "owner": "Bob_2"}]}
    run(a, a._handle_link_edit, "c2", dict(req))
    assert rejected(a)
    a.sent.clear()
    run(a, a._handle_link_edit, HOST, dict(req))
    assert not rejected(a)
    manual = [lk for lk in a.soullink_links.values() if lk.get("manual")]
    assert len(manual) == 1 and set(manual[0]["members"]) == {"Alice", "Bob"}
    # alte Auto-Gruppe ist leer und weg
    assert len(a.soullink_links) == 1
    removed = [m for _, m in a.sent if m.get("type") == "soullink_link_removed"]
    assert removed
    run(a, a._handle_link_edit, HOST, {
        "type": "soullink_link_edit", "op": "dissolve",
        "link_id": manual[0]["link_id"]})
    assert a.soullink_links == {}


def test_tod_manuell_lebendig_blockt_auto_tod():
    a = _make_arceus()
    link = _seed(a)
    a._process_death({"personality": 200, "owner": "Bob_2", "edition": 51})
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": False})
    assert (200, "Bob") not in a.soullink_deaths
    assert (200, "Bob") in a.soullink_alive_locks
    assert link["members"]["Bob"]["outcome"] == "caught"
    states = [m for _, m in a.sent if m.get("type") == "soullink_death_state"]
    assert states and states[0]["dead"] is False
    # Auto-Reset: Sperre weg
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": None})
    assert (200, "Bob") not in a.soullink_alive_locks


def test_tod_manuell_tot():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": True})
    death = a.soullink_deaths[(200, "Bob")]
    assert death["manual"] == 1 and death["partners"][0]["owner"] == "Alice"


def test_state_reload_clear_nur_host():
    a = _make_arceus()
    _seed(a)

    async def lauf():
        await a._handle_state_reload("c2", {"type": "server_state_reload", "clear": True})
        assert a.soullink_links
        await a._handle_state_reload(HOST, {"type": "server_state_reload", "clear": True})
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(lauf())
    assert a.soullink_links == {} and a.encounters == {}
    snaps = [m for _, m in a.sent if m.get("type") == "soullink_state_snapshot"]
    assert snaps and snaps[-1]["links"] == {}


def test_kaputte_payload_wird_abgelehnt_statt_exception():
    a = _make_arceus()
    _seed(a)
    run(a, a._run_edit, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": [1], "owner": "Bob_2"})
    run(a, a._run_edit, a._handle_link_edit, HOST, {
        "type": "soullink_link_edit", "op": "create", "members": "kaputt"})
    run(a, a._run_edit, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "unlock": 5})
    assert len(rejected(a)) == 3


def test_auto_outcome_dead_respektiert_lebendig_sperre():
    a = _make_arceus()
    link = _seed(a)
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": False})
    run(a, a._handle_encounter_outcome, "c2", {
        "type": "encounter_outcome", "personality": 200, "owner": "Bob_2",
        "outcome": "dead"})
    assert link["members"]["Bob"]["outcome"] == "caught"


def test_state_reload_ohne_clear_laedt_aus_db(tmp_path):
    a = _make_arceus()
    a.configsave = f"{tmp_path}/"

    async def lauf():
        await a.load_persistent_state()
        link = _seed(a)
        a._persist_link(link)
        a.soullink_links = {}
        await a._handle_state_reload(HOST, {"type": "server_state_reload", "clear": False})
        return link["link_id"]

    link_id = asyncio.run(lauf())
    assert set(a.soullink_links) == {link_id}


def test_doppelt_beanspruchter_slot_darf_nicht_editieren():
    a = _make_arceus()
    _seed(a)
    a.declared_player_ids_by_client["c3"] = {2}
    run(a, a._run_edit, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"lvl": 9}})
    assert rejected(a)
    assert a.encounters[(200, "Bob_2")]["lvl"] == 5


def test_death_edit_setzt_encounter_dead_und_sperre():
    a = _make_arceus()
    _seed(a)
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": True})
    e = a.encounters[(200, "Bob_2")]
    assert e["dead"] == 1 and "dead" in e["manual_fields"]
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": False})
    e = a.encounters[(200, "Bob_2")]
    assert e["dead"] == 0 and "dead" in e["manual_fields"]
    run(a, a._handle_death_edit, "c2", {
        "type": "soullink_death_edit", "personality": 200, "owner": "Bob_2",
        "dead": None})
    assert "dead" not in a.encounters[(200, "Bob_2")]["manual_fields"]
    assert (200, "Bob") not in a.soullink_alive_locks


def test_encounter_edit_dead_laeuft_ueber_tod_logik():
    a = _make_arceus()
    link = _seed(a)
    run(a, a._run_edit, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"dead": 1, "lvl": 8}})
    e = a.encounters[(200, "Bob_2")]
    assert e["dead"] == 1 and e["lvl"] == 8
    assert (200, "Bob") in a.soullink_deaths
    assert link["members"]["Bob"]["outcome"] == "dead"
    assert not rejected(a)


def test_changes_dead_typ_wird_geprueft():
    a = _make_arceus()
    _seed(a)
    run(a, a._run_edit, a._handle_encounter_edit, "c2", {
        "type": "encounter_edit", "personality": 200, "owner": "Bob_2",
        "changes": {"dead": "0"}})
    assert rejected(a)
    assert not a.encounters[(200, "Bob_2")].get("dead")
