// Placeholder-Config-Panel. In Phase 4 der TODO_twitch_extension.md wird
// hier der Pairing-Flow implementiert (Twitch Configuration Service +
// EBS-Endpoint zum Verknüpfen von channel_id ↔ Tracker-Session).
//
// Aktuell speichert dieses Skript den Code nur im Twitch-Configuration-Service
// (Segment "broadcaster"), damit die Pipe grundsätzlich funktioniert. Der
// EBS-seitige Registrierungs-Endpoint fehlt noch.

(function () {
    'use strict';

    var input   = document.getElementById('pairing-code');
    var saveBtn = document.getElementById('save-btn');
    var status  = document.getElementById('cfg-status');

    function setStatus(text) { status.textContent = text; }

    if (!window.Twitch || !window.Twitch.ext) {
        setStatus('Twitch-Helper nicht geladen — Seite muss im Twitch-Config-Iframe laufen.');
        return;
    }

    window.Twitch.ext.onAuthorized(function (auth) {
        setStatus('Verbunden als Channel ' + auth.channelId + '. Bereit für Pairing-Code.');
        saveBtn.disabled = false;

        // Existierenden Wert aus dem Twitch-Configuration-Service laden.
        var cfg = window.Twitch.ext.configuration.broadcaster;
        if (cfg && cfg.content) {
            try {
                var parsed = JSON.parse(cfg.content);
                if (parsed && parsed.pairing_code) {
                    input.value = parsed.pairing_code;
                }
            } catch (err) {
                console.warn('[tracker-ext-config] konnte gespeicherten Config-Wert nicht lesen', err);
            }
        }
    });

    saveBtn.addEventListener('click', function () {
        var code = (input.value || '').trim();
        if (!code) {
            setStatus('Bitte einen Code eingeben.');
            return;
        }
        var payload = JSON.stringify({ pairing_code: code });
        window.Twitch.ext.configuration.set('broadcaster', '1', payload);
        setStatus('Gespeichert. (TODO: EBS-Endpoint für Verknüpfung mit Tracker-Session fehlt noch.)');
    });
})();
