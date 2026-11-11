"""Todescounter-Overlay: Payload, Browser-Source-Routen, OBS-Textquellen."""
import asyncio
import json
from types import SimpleNamespace

from aiohttp.test_utils import make_mocked_request

from backend import death_counter as dc
from backend.classes.overlay_server import OverlayServer


def test_payload_summe_und_spieler_mit_null():
    p = dc.build_death_payload({"Steffen": 3, "web": 1, "Alt": 2},
                               {2: "web", 1: "Steffen", 3: "Neu"})
    assert [x["name"] for x in p["players"]] == ["Steffen", "web", "Neu", "Alt"]
    assert [x["count"] for x in p["players"]] == [3, 1, 0, 2]
    assert p["players"][2]["slot"] == 3 and p["players"][3]["slot"] is None
    assert p["total"] == 6
    assert dc.player_from_payload(p, 2)["name"] == "web"


def test_root_suffix_und_zweiter_slot_desselben_spielers():
    # Clientname "Ash_2" → Friedhof-Owner "Ash" (owner_root) → keine Phantom-Zeile
    p = dc.build_death_payload({"Ash": 2}, {1: "Ash_2"})
    assert [(x["name"], x["count"]) for x in p["players"]] == [("Ash_2", 2)]
    # Zwei Slots, gleicher Spieler: ein Zähler, beide Slot-URLs finden ihn
    p = dc.build_death_payload({"web": 1}, {1: "web", 3: "web"})
    assert len(p["players"]) == 1 and p["players"][0]["slots"] == [1, 3]
    assert dc.player_from_payload(p, 3)["count"] == 1


def test_obs_texte_und_input_namen_slug():
    p = dc.build_death_payload({"Steffen Ä": 2}, {1: "Steffen Ä"})
    texts = dc.obs_texts(p)
    assert texts[dc.OBS_TOTAL_INPUT] == "Gesamt: 2"
    name = dc.obs_input_name("Steffen Ä")
    assert name.startswith("deaths_") and " " not in name
    assert texts[name] == "Steffen Ä: 2"


def _server(counts, names):
    m = SimpleNamespace(snapshot_death_counts=lambda: dict(counts),
                        snapshot_player_names=lambda: dict(names))
    return OverlayServer(m, {}, {}, {"port": 0})


def test_overlay_data_und_seiten():
    srv = _server({"Steffen": 2}, {1: "Steffen", 2: "web"})

    async def run():
        resp = await srv._handle_deaths_data(make_mocked_request("GET", "/deaths/data"))
        data = json.loads(resp.body)
        assert data["total"] == 2 and data["players"][1] == {
            "name": "web", "slot": 2, "slots": [2], "count": 0, "obs_input": "deaths_web"}
        page = await srv._handle_deaths_page(
            make_mocked_request("GET", "/deaths/player/2", match_info={"slot": "2"}))
        assert "const SLOT = 2;" in page.text
        bad = await srv._handle_deaths_page(
            make_mocked_request("GET", "/deaths/player/x", match_info={"slot": "<x>"}))
        assert bad.status == 404
        all_page = await srv._handle_deaths_page(make_mocked_request("GET", "/deaths"))
        assert "const SLOT = null;" in all_page.text
    asyncio.run(run())


def test_notify_deaths_update_fuellt_queue():
    srv = _server({"web": 1}, {})

    async def run():
        q = asyncio.Queue()
        srv._deaths_sse_queues.append(q)
        await srv.notify_deaths_update()
        event = q.get_nowait()
        assert event.startswith("event: deaths\n")
        assert json.loads(event.split("data: ", 1)[1])["total"] == 1
    asyncio.run(run())


# --- OBS ---------------------------------------------------------------------

class _Resp:
    def __init__(self, ok, data=None):
        self._ok, self.responseData = ok, data or {}

    def ok(self):
        return self._ok


class _FakeWS:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls, self.batches = [], []

    def is_identified(self):
        return True

    async def call(self, req):
        self.calls.append((req.requestType, req.requestData))
        t, d = req.requestType, req.requestData or {}
        if t == "GetInputSettings":
            return _Resp(d["inputName"] in self.existing)
        if t == "GetInputKindList":
            return _Resp(True, {"inputKinds": ["text_gdiplus_v2", "text_gdiplus_v3", "image_source"]})
        if t == "GetCurrentProgramScene":
            return _Resp(True, {"sceneName": "Stream"})
        if t == "CreateInput":
            self.existing.add(d["inputName"])
            return _Resp(True, {"sceneItemId": 7})
        return _Resp(True)

    async def call_batch(self, batch):
        self.batches.append([(r.requestType, r.requestData) for r in batch])


def _obs(counts, names, rules=True, existing=()):
    from backend.classes.obs import OBS
    o = OBS.__new__(OBS)
    o.ws = _FakeWS(existing)
    o.logger = SimpleNamespace(info=lambda *a: None, debug=lambda *a: None,
                               warning=lambda *a: None, error=lambda *a: None)
    o._death_inputs_ready = set()
    o._text_input_kind = None
    o._scene_name = "Stream"
    o.munchlax = SimpleNamespace(snapshot_death_counts=lambda: dict(counts),
                                 snapshot_player_names=lambda: dict(names),
                                 _nuzlocke_rules_active=lambda: rules)

    async def _find_scene():
        return None
    o._find_scene = _find_scene
    return o


def test_obs_legt_quellen_ab_erstem_tod_an_und_setzt_text():
    o = _obs({"web": 1}, {1: "web"})
    asyncio.run(o.change_death_counter())
    created = [d for t, d in o.ws.calls if t == "CreateInput"]
    assert {c["inputName"] for c in created} == {"deaths_total", "deaths_web"}
    assert all(c["inputKind"] == "text_gdiplus_v3" for c in created)
    (batch,) = o.ws.batches
    assert ("SetInputSettings", {"inputName": "deaths_web",
                                 "inputSettings": {"text": "web: 1"}}) in batch


def test_obs_ohne_tod_legt_nichts_an_befuellt_aber_vorhandene():
    o = _obs({}, {1: "web"}, existing={"deaths_total"})
    asyncio.run(o.change_death_counter())
    assert not [1 for t, _ in o.ws.calls if t == "CreateInput"]
    (batch,) = o.ws.batches
    assert batch == [("SetInputSettings", {"inputName": "deaths_total",
                                           "inputSettings": {"text": "Gesamt: 0"}})]


def test_obs_nuzlocke_aus_legt_nichts_an():
    o = _obs({"web": 2}, {1: "web"}, rules=False)
    asyncio.run(o.change_death_counter())
    assert not [1 for t, _ in o.ws.calls if t == "CreateInput"]
    assert o.ws.batches == []
