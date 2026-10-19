"""Reine Hilfslogik fuer den Einstellungs-Screen (kein Kivy-Import).

Aus frontend/widgets/settingsmenu.py herausgeloest, damit sowohl die klassische
Kivy-UI als auch die KivyMD-UI dieselbe Overlay-Link-/Masking-/Team-Logik nutzen
und sie per pytest abgedeckt ist. Die eigentliche Widget-Erzeugung (Buttons,
Labels) bleibt in der jeweiligen View — hier entstehen nur die Daten dafuer.
"""
from __future__ import annotations

import socket
import traceback

from backend.classes.pokedex_db import PokedexDB
from backend.team_id import slug_team_id
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/backend.log')

# Platzhalter fuer maskierte IP-Anzeige (Stream-Leak-Schutz).
MASK_PLACEHOLDER = "••••••••"
MASK_EMPTY = "—"


def masked_placeholder(value: str) -> str:
    """Anzeige-Platzhalter fuer eine maskierte IP: Punkte wenn Wert vorhanden,
    sonst Gedankenstrich."""
    return MASK_PLACEHOLDER if value else MASK_EMPTY


def detect_lan_ip() -> str:
    """Ermittelt die outbound-LAN-IP ohne tatsaechlichen Netzwerkverkehr.
    UDP-Socket-Connect wertet nur die Routing-Tabelle aus."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def local_owner_string(pl: dict | None, rem: dict | None) -> str:
    """Owner-Schluessel des lokalen Spielers (name_<client_id>)."""
    your_name = pl.get('your_name', '') if pl else ''
    client_id = str(rem.get('client_id', 0)) if rem else '0'
    return PokedexDB.build_owner(your_name, client_id)


def resolve_team_ids(nuz: dict, pl: dict | None, rem: dict | None) -> list[tuple[str, str]]:
    """Liefert [(team_id_slug, label)] fuer den Team-Badge-URL-Bereich.

    - Soullink coop/versus: distinct team_ids aus soullink_team_membership,
      Label = raw team_id (vor Slug).
    - versus_ffa: pro expected_owner ein Team, Label = owner-Name.
    - Off/Nuzlocke: 1 impliziter Solo-Team-Slug (lokaler Owner), Label = "Team".

    Spiegelt arceus._effective_team_membership — bei Aenderung dort auch hier.
    """
    mode = (nuz.get("soullink_mode") or "off").lower()
    if mode == "coop":
        team_map = nuz.get("soullink_team_membership", {}) or {}
        if team_map:
            distinct: dict[str, str] = {}
            for _owner, raw_team in team_map.items():
                if raw_team is None:
                    continue
                slug = slug_team_id(raw_team)
                if slug not in distinct:
                    distinct[slug] = str(raw_team)
            return [(slug, f"Team {label}") for slug, label in distinct.items()]
        # Coop-Fallback: bei leerem team_membership alle expected_owners in ein
        # Bucket "coop". Slug muss literal "coop" sein, sonst URL-Mismatch zum Server.
        expected = nuz.get("soullink_expected_owners", []) or []
        if expected:
            return [("coop", "Team (Coop)")]
        return []
    if mode == "versus":
        team_map = nuz.get("soullink_team_membership", {}) or {}
        distinct = {}
        for _owner, raw_team in team_map.items():
            if raw_team is None:
                continue
            slug = slug_team_id(raw_team)
            if slug not in distinct:
                distinct[slug] = str(raw_team)
        return [(slug, f"Team {label}") for slug, label in distinct.items()]
    if mode == "versus_ffa":
        expected = nuz.get("soullink_expected_owners", []) or []
        return [(slug_team_id(o), f"Team {o}") for o in expected if o]
    owner = local_owner_string(pl, rem)
    return [(slug_team_id(owner), "Team (Solo)")]


def build_overlay_links(
    port: str,
    layout: str,
    badge_layout: str,
    player_count: int,
    two_pc: bool,
    nuz: dict,
    pl: dict | None,
    rem: dict | None,
    lan_ip: str | None = None,
) -> dict:
    """Baut die Overlay-Link-Struktur fuer die View (rein, keine Widgets).

    Rueckgabe:
      {
        "hint": str,                 # Hinweistext ueber den Buttons
        "two_pc": bool,
        "lan_ip": str,               # ermittelte LAN-IP ("" wenn keine)
        "hosts": [(label, host), …], # z.B. [("Lokal","localhost"),("LAN","192.168.1.5")]
        "players": [ {"label": "Spieler 1",
                      "links": [ {"host_label","team_url","badge_url"} ]} ],
        "teams":   [ {"label": "Team …",
                      "links": [ {"host_label","badge_url"} ]} ],
      }

    `lan_ip` kann fuer Tests injiziert werden; sonst wird es bei two_pc via
    detect_lan_ip() ermittelt.
    """
    hosts: list[tuple[str, str]] = [("Lokal", "localhost")]
    resolved_lan = ""
    if two_pc:
        resolved_lan = detect_lan_ip() if lan_ip is None else lan_ip
        if resolved_lan and resolved_lan != "localhost":
            hosts.append(("LAN", resolved_lan))

    if two_pc:
        if resolved_lan:
            hint = (f"LAN-IP: {resolved_lan} — 'LAN'-Buttons fuer OBS auf "
                    f"Streaming-PC im gleichen Netz.")
        else:
            hint = "LAN-IP nicht ermittelbar."
    else:
        hint = ("Fuer Zwei-PC-Setup: 'Streamsetup mit zwei PCs' bei den "
                "Sprite-Pfaden aktivieren.")

    team_layout_param = f"?layout={layout}" if layout != 'horizontal' else ""
    badge_layout_param = f"?layout={badge_layout}" if badge_layout != 'horizontal' else ""

    players: list[dict] = []
    for p in range(1, player_count + 1):
        links = []
        for host_label, host in hosts:
            links.append({
                "host_label": host_label,
                "team_url": f"http://{host}:{port}/player/{p}{team_layout_param}",
                "badge_url": f"http://{host}:{port}/player/{p}/badges{badge_layout_param}",
            })
        players.append({"label": f"Spieler {p}", "links": links})

    teams: list[dict] = []
    try:
        team_ids = resolve_team_ids(nuz, pl, rem)
    except Exception as err:
        logger.error(f"resolve_team_ids fehlgeschlagen: {err}")
        logger.error(traceback.format_exc())
        team_ids = []
    for team_id, team_label in team_ids:
        links = [{
            "host_label": host_label,
            "badge_url": f"http://{host}:{port}/team/{team_id}/badges{badge_layout_param}",
        } for host_label, host in hosts]
        teams.append({"label": team_label, "links": links})

    return {
        "hint": hint,
        "two_pc": two_pc,
        "lan_ip": resolved_lan,
        "hosts": hosts,
        "players": players,
        "teams": teams,
    }
