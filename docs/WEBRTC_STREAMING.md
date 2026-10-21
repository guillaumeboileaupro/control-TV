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

Le récepteur se connecte à `adresse:port`, envoie une ligne JSON `token`, reçoit l'offre SDP, puis renvoie une ligne contenant le refus explicite ou la réponse SDP. Le serveur disparaît après cet échange. Le média est chiffré par DTLS-SRTP. La signalisation TCP n'est pas chiffrée : le jeton protège contre une connexion arbitraire mais pas contre un attaquant capable d'écouter le LAN ; ajouter TLS ou une preuve de possession challenge/réponse est bloquant avant publication.

## Validation automatisée et limites

Le test d'intégration Rust crée deux pairs réels, négocie ICE/SDP/DTLS/SRTP sur la machine, transmet une unité H.264 Annex-B et vérifie la réception RTP. Les tests de signalisation utilisent uniquement la boucle locale. L'APK est compilé mais jamais installé.

La capture réelle Ubuntu, le décodage matériel Android TV, l'audio/vidéo synchronisé, D2, la reprise réseau et la latence physique restent `NOT RUN`. Le flux FFmpeg doit encore être découpé en access units H.264 et paquets Opus au lieu de transmettre des blocs de pipe arbitraires ; il ne constitue donc pas encore une recette média Ubuntu→TV valide.

## Recette matérielle à autoriser

Pour chaque Android TV : installer l'APK explicitement autorisé, accepter une nouvelle session, tester D1 puis D2, arrêt et reconnexion. Mesurer par caméra/chronomètre la latence physique médiane, p95 et maximale, FPS, pertes, dérive A/V, CPU/GPU et stabilité. Un RTT WebRTC n'est jamais une mesure bout en bout. Restaurer la topologie d'écran et confirmer l'absence de processus, socket ou session restante.
