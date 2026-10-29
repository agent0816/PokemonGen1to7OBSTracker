"""Tests fuer backend/settings_view.py (reine Overlay-Link-/Team-/Mask-Logik)."""
from backend import settings_view as sv


def test_masked_placeholder():
    assert sv.masked_placeholder("1.2.3.4") == sv.MASK_PLACEHOLDER
    assert sv.masked_placeholder("") == sv.MASK_EMPTY


def test_local_owner_string():
    assert sv.local_owner_string({"your_name": "Alice"}, {"client_id": 2}) == "Alice_2"
    # Defaults bei fehlenden Dicts
    assert sv.local_owner_string(None, None) == "_0"


def test_resolve_team_ids_solo_off():
    teams = sv.resolve_team_ids({}, {"your_name": "Alice"}, {"client_id": 0})
    assert teams == [("Alice_0", "Team (Solo)")]


def test_resolve_team_ids_coop_fallback():
    nuz = {"run_mode": "coop", "soullink_expected_owners": ["Alice", "Bob"]}
    assert sv.resolve_team_ids(nuz, None, None) == [("coop", "Team (Coop)")]


def test_resolve_team_ids_coop_membership():
    nuz = {
        "run_mode": "coop",
        "soullink_team_membership": {"Alice": "Rot", "Bob": "Rot", "Carol": "Blau"},
    }
    teams = sv.resolve_team_ids(nuz, None, None)
    # distinct nach Slug, Reihenfolge der ersten Nennung
    assert teams == [("Rot", "Team Rot"), ("Blau", "Team Blau")]


def test_resolve_team_ids_versus_ffa():
    nuz = {"run_mode": "versus_ffa", "soullink_expected_owners": ["Alice", "Bob"]}
    teams = sv.resolve_team_ids(nuz, None, None)
    assert teams == [("Alice", "Team Alice"), ("Bob", "Team Bob")]


def test_build_overlay_links_single_pc():
    data = sv.build_overlay_links(
        port="43888", layout="horizontal", badge_layout="horizontal",
        player_count=2, two_pc=False, nuz={}, pl={"your_name": "Alice"},
        rem={"client_id": 0},
    )
    assert data["two_pc"] is False
    assert data["hosts"] == [("Lokal", "localhost")]
    assert "zwei PCs" in data["hint"]
    assert len(data["players"]) == 2
    first = data["players"][0]["links"][0]
    assert first["team_url"] == "http://localhost:43888/player/1"
    assert first["badge_url"] == "http://localhost:43888/player/1/badges"
    # Solo-Team vorhanden
    assert data["teams"][0]["links"][0]["badge_url"] == \
        "http://localhost:43888/team/Alice_0/badges"


def test_build_overlay_links_two_pc_with_lan():
    data = sv.build_overlay_links(
        port="43888", layout="2x3", badge_layout="4x4",
        player_count=1, two_pc=True, nuz={}, pl={"your_name": "Alice"},
        rem={"client_id": 0}, lan_ip="192.168.1.5",
    )
    assert data["lan_ip"] == "192.168.1.5"
    assert ("LAN", "192.168.1.5") in data["hosts"]
    assert "192.168.1.5" in data["hint"]
    links = data["players"][0]["links"]
    # layout-Parameter angehaengt (nicht horizontal)
    assert links[0]["team_url"] == "http://localhost:43888/player/1?layout=2x3"
    assert links[0]["badge_url"] == "http://localhost:43888/player/1/badges?layout=4x4"
    # LAN-Host hat eigenen Link-Satz
    assert links[1]["host_label"] == "LAN"
    assert links[1]["team_url"] == "http://192.168.1.5:43888/player/1?layout=2x3"


def test_build_overlay_links_two_pc_no_lan():
    data = sv.build_overlay_links(
        port="43888", layout="horizontal", badge_layout="horizontal",
        player_count=1, two_pc=True, nuz={}, pl={"your_name": "A"},
        rem={"client_id": 0}, lan_ip="",
    )
    assert data["hosts"] == [("Lokal", "localhost")]
    assert data["hint"] == "LAN-IP nicht ermittelbar."


def test_build_overlay_links_lan_equals_localhost_not_added():
    data = sv.build_overlay_links(
        port="43888", layout="horizontal", badge_layout="horizontal",
        player_count=1, two_pc=True, nuz={}, pl={"your_name": "A"},
        rem={"client_id": 0}, lan_ip="localhost",
    )
    # 'localhost' als LAN-IP wird nicht als zweiter Host dupliziert
    assert data["hosts"] == [("Lokal", "localhost")]
