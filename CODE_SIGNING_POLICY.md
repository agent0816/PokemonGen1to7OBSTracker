# Code Signing Policy

_Letzte Aktualisierung: 2026-10-24_

Die Windows-Releases von **PokemonGen1to7OBSTracker** werden digital signiert, damit
Nutzer bestätigen können, dass die ausgeführte Datei unverändert und tatsächlich aus
diesem Projekt stammt.

Das kostenlose Code-Signing-Zertifikat wird im Rahmen des **SignPath Foundation**-
Programms für Open-Source-Projekte bereitgestellt.

## Zertifikat

| | |
|---|---|
| **Publisher / Herausgeber** | SignPath Foundation |
| **Zertifikatstyp** | Code Signing Certificate |
| **Signatur-Algorithmus** | SHA-256 |
| **Zeitstempel** | SignPath Foundation Timestamp Service |

Das Zertifikat ist auf die **SignPath Foundation** ausgestellt. In den Datei-Eigenschaften
erscheint daher „SignPath Foundation" als Herausgeber, nicht der Projekt-Name — das ist
beim SignPath-Foundation-Programm so vorgesehen und korrekt.

## Signatur prüfen

**Windows (Explorer):**
Rechtsklick auf die `.exe` → _Eigenschaften_ → Reiter _Digitale Signaturen_ → Signierer
muss **„SignPath Foundation"** sein.

**PowerShell:**
```powershell
Get-AuthenticodeSignature .\PokemonGen1to7OBSTracker.exe | Format-List
```
`Status` muss `Valid` sein und `SignerCertificate` auf die SignPath Foundation verweisen.

## Build-Verifizierung

- Releases werden ausschließlich über **GitHub Actions** aus dem offiziellen Repository
  [agent0816/PokemonGen1to7OBSTracker](https://github.com/agent0816/PokemonGen1to7OBSTracker)
  gebaut.
- Der Build erzeugt die Onefile-Binary mit **Nuitka** (siehe `nuitka_build.ps1`).
- SignPath verifiziert, dass signierte Binaries aus diesem offiziellen Repository und
  dessen GitHub-Actions-Pipeline stammen, bevor eine Signatur erteilt wird.
- Es werden keine lokal (außerhalb der CI) gebauten Artefakte zur Signatur eingereicht.

## Team-Rollen

| Name | Rolle | Verantwortung |
|---|---|---|
| agent0816 | Author | Pflegt den Quellcode und das Repository |
| agent0816 | Approver | Gibt Signatur-Anfragen manuell frei |

## Sicherheitspraktiken

- Private Signaturschlüssel liegen im **Hardware Security Module (HSM)** der SignPath
  Foundation und verlassen dieses nie.
- **Jede** Signatur-Anfrage erfordert eine manuelle Freigabe durch einen Approver; eine
  einzelne Ablehnung bricht die Anfrage ab.
- Die GitHub-Accounts mit Signatur-Berechtigung haben **Zwei-Faktor-Authentifizierung
  (2FA)** aktiviert.
- Alle Signaturen werden mit einem Zeitstempel versehen und bleiben damit auch nach
  Ablauf des Zertifikats gültig.

## Datenschutz

PokemonGen1to7OBSTracker überträgt keine personenbezogenen Daten zu Analyse- oder
Tracking-Zwecken. Netzwerkverbindungen beschränken sich auf:

- **Update-Prüfung** gegen die GitHub-Releases dieses Projekts (tufup).
- **Sprite-Repository** per git clone/pull von GitHub ([agent0816/sprites](https://github.com/agent0816/sprites)).
- **Emulator-Anbindung** lokal (BizHawk via TCP, Citra/Azahar via UDP).
- **Overlay/OBS** lokal (HTTP-/WebSocket-Server bzw. OBS-WebSocket).
- **Remote-Multiplayer** — nur wenn vom Nutzer aktiviert, zu einem vom Nutzer selbst
  angegebenen Server.

Es findet keine Telemetrie statt.

## Verdächtige Datei melden

Wird eine manipulierte oder schädliche Binary vermutet:

1. Die Datei **nicht ausführen**.
2. Meldung über ein [GitHub Issue](https://github.com/agent0816/PokemonGen1to7OBSTracker/issues)
   bzw. eine GitHub Security Advisory, mit **SHA-256-Hash** der Datei und der
   **Download-Quelle**.

---

_Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/)._
