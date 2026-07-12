// Skeleton-Viewer für die Pokemon-Tracker Twitch-Extension.
// Zweck dieser Datei: den Datenweg beweisen (Auth → State-Fetch → PubSub-Push).
// UI-Aufbau kommt in Phase 3 der TODO_twitch_extension.md.

(function () {
    'use strict';

    var EBS_BASE_URL = window.EBS_BASE_URL || '';
    if (!EBS_BASE_URL) {
        console.error('[tracker-ext] EBS_BASE_URL nicht gesetzt (ebs-endpoint.js prüfen).');
    }

    var els = {
        root:      document.getElementById('root'),
        statusDot: document.getElementById('status-dot'),
        statusTxt: document.getElementById('status-text'),
        state:     document.getElementById('raw-state'),
        lastEvent: document.getElementById('last-event-body'),
    };

    var latestState = null;
    var currentAuth = null;

    function setStatus(kind, text) {
        els.statusDot.className = 'dot dot-' + kind;
        els.statusTxt.textContent = text;
    }

    // Wird sowohl beim initialen Load als auch nach jedem PubSub-Event aufgerufen.
    // Absichtlich datenzentriert (keine DOM-Abhängigkeit auf globalen State),
    // damit man die Funktion für einen Sanity-Check aus der Devtools-Konsole
    // mit einem Mock-Objekt füttern kann.
    function renderState(stateObj) {
        latestState = stateObj;
        try {
            els.state.textContent = JSON.stringify(stateObj, null, 2);
        } catch (err) {
            els.state.textContent = String(err);
        }
        // Skeleton bleibt aus praktischen Gründen für Debug immer aufgeklappt.
        // In Phase 3 wird das durch den echten Klapp-/Hover-Trigger ersetzt.
        els.root.classList.remove('collapsed');
    }

    function applyPubSubMessage(msg) {
        if (!latestState) {
            latestState = { channel_id: currentAuth ? currentAuth.channelId : null,
                            teams: {}, bags: {}, pokedex: {},
                            active_player_id: null, session_ended: false };
        }
        switch (msg.type) {
            case 'team_update':
                latestState.teams[msg.payload.player_id] = msg.payload;
                latestState.session_ended = false;
                break;
            case 'bag_update':
                latestState.bags[msg.payload.player_id] = msg.payload;
                latestState.session_ended = false;
                break;
            case 'pokedex_update':
                latestState.pokedex[msg.payload.player_id] = msg.payload;
                latestState.session_ended = false;
                break;
            case 'player_switch':
                latestState.active_player_id = msg.payload.active_player_id;
                break;
            case 'session_end':
                latestState.session_ended = true;
                break;
            default:
                console.warn('[tracker-ext] unbekannter PubSub-Typ', msg);
        }
        latestState.updated_at = Date.now();
        renderState(latestState);
    }

    function fetchInitialState(auth) {
        if (!EBS_BASE_URL) return Promise.resolve();
        var url = EBS_BASE_URL + '/state/' + encodeURIComponent(auth.channelId);
        return fetch(url, {
            headers: { 'Authorization': 'Bearer ' + auth.token },
        }).then(function (res) {
            if (!res.ok) {
                throw new Error('State-Fetch HTTP ' + res.status);
            }
            return res.json();
        }).then(function (data) {
            renderState(data);
            setStatus('connected', 'Verbunden (Channel ' + auth.channelId + ')');
        }).catch(function (err) {
            console.error('[tracker-ext] Initial-State-Fetch fehlgeschlagen', err);
            setStatus('disconnected', 'State-Fetch fehlgeschlagen — Tracker offline?');
            els.state.textContent = 'Offline: ' + String(err && err.message || err);
        });
    }

    function subscribePubSub() {
        // Der zweite Parameter im Callback ist der contentType; wir schicken
        // vom EBS aus reines JSON. `target` ist immer 'broadcast', da wir nur
        // dieses Topic benutzen.
        window.Twitch.ext.listen('broadcast', function (_target, _contentType, message) {
            var parsed = null;
            try {
                parsed = JSON.parse(message);
            } catch (err) {
                console.error('[tracker-ext] PubSub-Message nicht JSON', err, message);
                return;
            }
            try {
                els.lastEvent.textContent = JSON.stringify(parsed, null, 2);
            } catch (_err) { /* nur Debug-Anzeige, egal wenn's scheitert */ }
            applyPubSubMessage(parsed);
        });
    }

    function boot() {
        if (!window.Twitch || !window.Twitch.ext) {
            setStatus('disconnected', 'Twitch-Helper nicht geladen');
            console.error('[tracker-ext] window.Twitch.ext fehlt — läuft die Seite im Extension-Iframe?');
            return;
        }

        // Extensions können bei Offline-Zustand des Broadcasts weiterlaufen —
        // wir loggen es aber, damit man später Race-Conditions einordnen kann.
        window.Twitch.ext.onContext(function (ctx) {
            if (ctx && ctx.arePlayerControlsVisible === false) {
                // Placeholder — für UI-Feinheiten in Phase 3.
            }
        });

        window.Twitch.ext.onAuthorized(function (auth) {
            currentAuth = auth;
            setStatus('waiting', 'Auth erhalten, hole State …');
            subscribePubSub();
            fetchInitialState(auth);
        });

        window.Twitch.ext.onError(function (err) {
            console.error('[tracker-ext] Twitch.ext-Fehler', err);
            setStatus('disconnected', 'Twitch-Fehler: ' + (err && err.message || err));
        });
    }

    // Debug-Hilfe für die Devtools-Konsole:
    //   TrackerExtDebug.render({teams:{...}, bags:{}, pokedex:{}})
    //   TrackerExtDebug.apply({type:'team_update', payload:{...}})
    window.TrackerExtDebug = {
        render: renderState,
        apply: applyPubSubMessage,
        get state() { return latestState; },
        get auth()  { return currentAuth; },
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
