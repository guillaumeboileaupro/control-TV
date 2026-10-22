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

La détection de capacité réutilise le `TvControl` et l'inventaire d'applications existants, sur le seul identifiant sélectionné. La disponibilité Cast confirme le chemin HLS ; seul le paquet exact `io.github.guillaumeboileaupro.controltv.receiver`, effectivement listé par la TV, confirme l'installation et l'accessibilité WebRTC. Un inventaire ADB inaccessible reste « capacité non confirmée », jamais « application absente » ni compatibilité supposée.

Le bail D1/D2 appartient au contrôleur Python existant. Sa préparation est annulable : une fermeture invalide sa génération, attend la fin bornée de l'acquisition et empêche toute publication tardive. Une extension dont la restauration échoue reste suivie afin que l'arrêt puisse être retenté ; le diagnostic conserve simultanément une erreur du moteur et une erreur de restauration.

La commande Tauri acquiert ce bail avant de créer le moteur, utilise séparément sa taille source et la taille d'encodage demandée, puis ne l'active qu'après la négociation et le démarrage média. Toute erreur de préparation du moteur, négociation, activation, fin de média ou arrêt déclenche sa libération. Les coordonnées X11 ne sont donc jamais acceptées depuis l'interface.

Un verrou de cycle de vie commun empêche `start`, `stop` et le traitement d'une fin de média de se dépasser pendant la transaction. Si la libération échoue au niveau du bridge, le diagnostic conserve à la fois la cause WebRTC et cette panne ; une réponse Python qui contient déjà les deux n'est pas répétée.

La carte « Screen to TV » conserve le choix d'équipement global et ajoute un sélecteur natif Auto/WebRTC/HLS. Elle relit les capacités avant le démarrage ; Auto préfère uniquement un WebRTC confirmé et revient sinon à HLS, alors qu'un choix WebRTC explicite échoue sans repli. Le suivi affiche le transport réellement utilisé et rejette toute réponse antérieure à une nouvelle opération Start/Stop. Les identifiants éphémères présents dans la réponse interne ne sont jamais affichés ni écrits dans les journaux de l'interface. Leur remise automatique au récepteur reste à concevoir sur un canal confidentiel et authentifié ; le formulaire manuel actuel et une commande ADB contenant ces valeurs ne répondent pas à cette exigence.

Pour D1/D2, le contrôleur Python existant acquiert un bail avant le démarrage Rust. Le bail contient uniquement la route locale, le display X11 et la géométrie de la source ; aucun secret de session. En D2, il possède l'instance du gestionnaire d'extension existant jusqu'à la libération et restaure le bureau sur arrêt ou fermeture du bridge. FFmpeg capture la taille réelle de cette source puis la met explicitement à l'échelle vers la résolution vidéo demandée.

Une première connexion invalide consomme encore cette écoute à usage unique : l'utilisateur doit relancer la session. La reprise bornée après perte du réseau reste à implémenter ; aucun rejeu automatique de commande ou de secret n'est effectué.

## Validation automatisée et limites

Le test d'intégration Rust crée deux pairs réels, négocie ICE/SDP/DTLS/SRTP sur la machine, transmet simultanément plusieurs unités H.264 Annex-B et plusieurs paquets Opus, puis vérifie cinq paquets RTP reçus sur chaque piste. Les tests de signalisation TLS, d'empreinte invalide et d'anti-rejeu utilisent uniquement la boucle locale. Les sorties FFmpeg sont découpées en unités d'accès H.264 délimitées par AUD et en paquets Opus extraits de pages Ogg ; les files sont bornées, la vidéo obsolète peut être abandonnée et l'audio applique une contre-pression. L'APK est compilé mais jamais installé.

La capture réelle Ubuntu, le décodage matériel Android TV, l'audio/vidéo synchronisé, la stabilité sur plusieurs minutes, D2, la reprise réseau et la latence physique restent `NOT RUN`. Le test local prouve le transport concurrent et chiffré, pas la recette média Ubuntu→TV ni le rendu matériel.

## Recette matérielle à autoriser

Pour chaque Android TV : installer l'APK explicitement autorisé, accepter une nouvelle session, tester D1 puis D2, arrêt et reconnexion. Mesurer par caméra/chronomètre la latence physique médiane, p95 et maximale, FPS, pertes, dérive A/V, CPU/GPU et stabilité. Un RTT WebRTC n'est jamais une mesure bout en bout. Restaurer la topologie d'écran et confirmer l'absence de processus, socket ou session restante.
