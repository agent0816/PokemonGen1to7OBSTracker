// EBS-Endpoint — bewusst getrennt von viewer.js, damit Dev/Test/Prod
// dieselbe JS-Codebasis teilen und nur diese eine Datei getauscht wird.
//
// Wichtig: Twitch-Extensions haben strikte CSP; die hier eingetragene Domain
// muss im Twitch-Extension-Manifest unter "Testing Base URI" (Hosted Test)
// bzw. später "Whitelisted Config URLs"/"URLs Used" freigeschaltet sein,
// sonst blockiert der Browser den fetch().
window.EBS_BASE_URL = 'http://localhost:8081';
