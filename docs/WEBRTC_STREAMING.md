# Moteur Rust/WebRTC et récepteur Android TV

## Décision

Le chemin faible latence complète, sans remplacer, `ControlService` et le repli HLS. Le crate `webrtc-engine` utilise `webrtc-rs` 0.14 : sa pile asynchrone inclut SDP, ICE, DTLS, SRTP, RTP/RTCP et s'intègre au runtime Tokio déjà utilisé par Tauri. La nouvelle 0.21 sans I/O a été écartée pour cette première tranche car elle imposerait au produit d'implémenter lui-même sockets et horloges. Android utilise `io.github.webrtc-sdk:android:150.7871.01`, libwebrtc précompilé avec décodage et rendu natifs.

## Limites de responsabilités

- Python garde découverte, sélection X11, D1/D2 et restauration de l'affichage.
- Tauri vérifie le consentement et construit une session Rust unique.
- `FfmpegMediaSource` lance sans shell une capture X11 H.264 baseline/zerolatency et, si demandé, le moniteur PulseAudio en Opus low-delay. Les enfants sont `kill_on_drop` et explicitement attendus à l'arrêt.
- `WebRtcTransport` crée les pistes H.264/Opus, négocie SDP/ICE et draine RTCP.
- `PairingServer` écoute uniquement l'adresse choisie, sur un port éphémère, pour un échange unique borné. Un jeton aléatoire de 48 caractères est requis et masqué dans `Debug`.
- `SessionController` impose une session, un délai de négociation et une durée maximale d'une heure.

## Protocole local

Chaque session crée un certificat TLS éphémère. Le récepteur se connecte à `adresse:port`, vérifie son empreinte SHA-256 saisie avec les paramètres de session, puis envoie dans ce canal chiffré une ligne JSON contenant le jeton. Il reçoit l'offre SDP et renvoie, après rassemblement ICE complet, une ligne contenant le refus explicite ou la réponse SDP finale. Le certificat épinglé authentifie l'émetteur ; le jeton unique authentifie le récepteur et n'apparaît ni dans `Debug` ni dans les erreurs. Le serveur disparaît après cet échange, ce qui interdit sa réutilisation. Le média est ensuite chiffré par DTLS-SRTP.

Une première connexion invalide consomme encore cette écoute à usage unique : l'utilisateur doit relancer la session. La reprise bornée après perte du réseau reste à implémenter ; aucun rejeu automatique de commande ou de secret n'est effectué.

## Validation automatisée et limites

Le test d'intégration Rust crée deux pairs réels, négocie ICE/SDP/DTLS/SRTP sur la machine, transmet simultanément plusieurs unités H.264 Annex-B et plusieurs paquets Opus, puis vérifie cinq paquets RTP reçus sur chaque piste. Les tests de signalisation TLS, d'empreinte invalide et d'anti-rejeu utilisent uniquement la boucle locale. Les sorties FFmpeg sont découpées en unités d'accès H.264 délimitées par AUD et en paquets Opus extraits de pages Ogg ; les files sont bornées, la vidéo obsolète peut être abandonnée et l'audio applique une contre-pression. L'APK est compilé mais jamais installé.

La capture réelle Ubuntu, le décodage matériel Android TV, l'audio/vidéo synchronisé, la stabilité sur plusieurs minutes, D2, la reprise réseau et la latence physique restent `NOT RUN`. Le test local prouve le transport concurrent et chiffré, pas la recette média Ubuntu→TV ni le rendu matériel.

## Recette matérielle à autoriser

Pour chaque Android TV : installer l'APK explicitement autorisé, accepter une nouvelle session, tester D1 puis D2, arrêt et reconnexion. Mesurer par caméra/chronomètre la latence physique médiane, p95 et maximale, FPS, pertes, dérive A/V, CPU/GPU et stabilité. Un RTT WebRTC n'est jamais une mesure bout en bout. Restaurer la topologie d'écran et confirmer l'absence de processus, socket ou session restante.
