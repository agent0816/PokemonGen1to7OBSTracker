"""Automatischer Encounter-Import aus Team + Boxen (Host-Sync, Testrunde 2026-11-03).

Der Host stoesst ueber den Server bei allen Clients die Erfassung an; jeder
Client fuehrt hier fuer seine lokalen Spieler denselben Ablauf wie der
Import-Dialog aus — nur ohne Vorschau, mit der Standard-Auswahl (alle
Kandidaten, erstes Encounter je Route wie vorgeschlagen).

Kein Kivy-Import. Laeuft auf dem Kivy-Loop (BizHawk-Lesen via bh_submit),
Munchlax-Aufrufe gehen per submit_cross_thread auf dessen Loop.
"""
from __future__ import annotations

import asyncio
import traceback

from backend.encounter_import import (
    auto_select, build_encounters, collect_candidates, group_by_route, own_trainer,
)
from backend.logging_setup import get_logger
from frontend.box_reader import read_boxes_for_player

logger = get_logger(__name__, 'logs/encountermenu.log')

CROSS_TIMEOUT = 30
# Haengender Emulator darf den Auto-Import nicht endlos blockieren (sonst
# bleibt der "laeuft bereits"-Guard im Frontend gesetzt).
BOX_READ_TIMEOUT = 20


async def _cross(munchlax, coro):
    try:
        future = munchlax.submit_cross_thread(coro)
    except Exception:
        coro.close()
        raise
    # shield: Timeout bricht nur das Warten ab, nicht import_encounters mitten
    # im Insert/encounter_sync (wie send_munchlax_edit).
    return await asyncio.wait_for(asyncio.shield(future), CROSS_TIMEOUT)


async def import_player(munchlax, pl: dict, citra, bizhawk, pid: int) -> dict:
    """Auto-Import fuer einen lokalen Spieler. Rueckgabe {player, ok, count, msg}."""
    result = {"player": pid, "ok": False, "count": 0, "msg": ""}
    boxes_note = ""
    try:
        boxes, error = await asyncio.wait_for(
            read_boxes_for_player(pl, citra, bizhawk, pid), BOX_READ_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning(f"Auto-Import: Boxen lesen Spieler {pid} > {BOX_READ_TIMEOUT}s")
        boxes, error = None, "Timeout"
    except Exception as err:
        logger.error(f"Auto-Import: Boxen lesen Spieler {pid} fehlgeschlagen: "
                     f"{traceback.format_exc()}")
        boxes, error = None, f"{type(err).__name__}"
    if boxes:
        await _cross(munchlax, munchlax.update_boxes(pid, boxes))
    else:
        # Wie der Dialog: ohne frische Boxen mit dem gespeicherten Stand weiter
        boxes_note = " (gespeicherte Boxen)"
        logger.info(f"Auto-Import Spieler {pid}: Boxen nicht lesbar ({error}), "
                    f"nutze gespeicherten Stand")

    ctx = await _cross(munchlax, munchlax.encounter_import_context(pid))
    if not ctx:
        result["msg"] = "Edition oder Datenbank nicht verfügbar"
        return result
    team = list(munchlax.snapshot_sorted_teams().get(pid) or [])[:6]
    stored_boxes = munchlax.snapshot_boxes(pid)
    everything = team + [p for box in stored_boxes for p in (box or [])]
    cands, skipped = collect_candidates(
        team, stored_boxes, own_trainer(everything), set(ctx.get("existing_pvs") or ()))
    if not cands:
        if skipped.get("no_pv") and not skipped.get("known"):
            result["msg"] = "Gen 1/2 nicht unterstützt"
        else:
            result["ok"] = True
            result["msg"] = f"nichts Neues{boxes_note}"
        return result
    groups = group_by_route(cands, set(ctx.get("routes_with_first") or ()))
    selected, first = auto_select(groups)
    encounters = build_encounters(groups, selected, first,
                                  str(ctx["owner"]), int(ctx["edition"]))
    ok, count, msg = await _cross(munchlax, munchlax.import_encounters(pid, encounters))
    result.update(ok=bool(ok), count=int(count or 0), msg=f"{msg}{boxes_note}")
    logger.info(f"Auto-Import Spieler {pid}: {result} (Kandidaten={len(cands)}, "
                f"übersprungen={skipped})")
    return result


async def run_auto_import(munchlax, pl: dict, citra, bizhawk) -> list[dict]:
    """Auto-Import fuer alle lokalen Spieler; Fehler pro Spieler abgefangen."""
    results: list[dict] = []
    try:
        players = list(munchlax.owned_player_ids())
    except Exception:
        logger.error(f"Auto-Import: lokale Slots nicht lesbar: {traceback.format_exc()}")
        return results
    for pid in players:
        try:
            results.append(await import_player(munchlax, pl, citra, bizhawk, pid))
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.error(f"Auto-Import Spieler {pid} fehlgeschlagen: {type(err)},{err}")
            logger.error(traceback.format_exc())
            results.append({"player": pid, "ok": False, "count": 0,
                            "msg": f"Fehler ({type(err).__name__})"})
    return results


def summary_text(per_client: list[tuple[str, list[dict]]]) -> tuple[str, bool]:
    """Sammel-Snackbar fuer den Host ueber alle eingegangenen Client-Ergebnisse."""
    lines = []
    all_ok = True
    for name, results in per_client:
        text, ok = result_text(name, results)
        lines.append(text)
        all_ok = all_ok and ok
    return "Encounter-Erfassung: " + " | ".join(lines), all_ok


def result_text(client_name: str, results: list[dict]) -> tuple[str, bool]:
    """Snackbar-Text fuer den Host + ob alles ok war."""
    if not results:
        return f"{client_name}: kein lokaler Spieler", True
    parts = []
    all_ok = True
    for r in results:
        all_ok = all_ok and bool(r.get("ok"))
        count = int(r.get("count") or 0)
        detail = f"{count} übernommen" if count else (r.get("msg") or "—")
        if r.get("player") is None:
            parts.append(str(detail))
        else:
            parts.append(f"Slot {r.get('player')}: {detail}")
    return f"{client_name} – " + ", ".join(parts), all_ok
