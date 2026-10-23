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

La détection de capacité réutilise le `TvControl` et l'inventaire d'applications existants, sur le seul identifiant sélectionné. La disponibilité Cast confirme le chemin HLS ; le paquet exact `io.github.guillaumeboileaupro.controltv.receiver`, effectivement listé par la TV, et une lecture réussie du canal Android Remote appairé sont tous deux requis pour confirmer l'accessibilité WebRTC. Un inventaire ADB ou un canal Remote inaccessible reste « capacité non confirmée », jamais « application absente » ni compatibilité supposée.

Le bail D1/D2 appartient au contrôleur Python existant. Sa préparation est annulable : une fermeture invalide sa génération, attend la fin bornée de l'acquisition et empêche toute publication tardive. Une extension dont la restauration échoue reste suivie afin que l'arrêt puisse être retenté ; le diagnostic conserve simultanément une erreur du moteur et une erreur de restauration.

La commande Tauri acquiert ce bail avant de créer le moteur, utilise séparément sa taille source et la taille d'encodage demandée, puis ne l'active qu'après la négociation et le démarrage média. Toute erreur de préparation du moteur, négociation, activation, fin de média ou arrêt déclenche sa libération. Les coordonnées X11 ne sont donc jamais acceptées depuis l'interface.

Un verrou de cycle de vie commun empêche `start`, `stop` et le traitement d'une fin de média de se dépasser pendant la transaction. Si la libération échoue au niveau du bridge, le diagnostic conserve à la fois la cause WebRTC et cette panne ; une réponse Python qui contient déjà les deux n'est pas répétée.

La carte « Screen to TV » conserve le choix d'équipement global et ajoute un sélecteur natif Auto/WebRTC/HLS. Elle relit les capacités avant le démarrage ; Auto préfère uniquement un WebRTC confirmé et revient sinon à HLS, alors qu'un choix WebRTC explicite échoue sans repli. Le suivi affiche le transport réellement utilisé et rejette toute réponse antérieure à une nouvelle opération Start/Stop.

Le pairage n'est jamais renvoyé à la fenêtre. La sonde de capacité exige le paquet récepteur exact et le canal Android Remote appairé. Le shell lance ensuite ce paquet par le gestionnaire d'applications existant, sans secret, exige sa confirmation au premier plan, puis envoie une enveloppe compacte `ctv1` uniquement si Android Remote signale un champ actif. Ce canal TLS est déjà authentifié par l'appairage propriétaire. Le récepteur utilise un champ mot de passe, valide strictement version, adresse, port, jeton et empreinte, efface immédiatement sa valeur puis ouvre la signalisation TLS épinglée. Le contenu ne passe ni dans ADB ni dans les résultats ou erreurs du contrôle distant, qui ne consignent que sa longueur. Si l'application ou le champ actif ne sont pas confirmés, rien n'est saisi et la session échoue en libérant D1/D2 au lieu d'attendre indéfiniment. Ce parcours est testé sur fakes/JVM mais reste à confirmer sur les deux téléviseurs.

Pour D1/D2, le contrôleur Python existant acquiert un bail avant le démarrage Rust. Le bail contient uniquement la route locale, le display X11 et la géométrie de la source ; aucun secret de session. En D2, il possède l'instance du gestionnaire d'extension existant jusqu'à la libération et restaure le bureau sur arrêt ou fermeture du bridge. FFmpeg capture la taille réelle de cette source puis la met explicitement à l'échelle vers la résolution vidéo demandée.

Une première connexion invalide consomme encore cette écoute à usage unique : l'utilisateur doit relancer la session. La reprise bornée après perte du réseau reste à implémenter ; aucun rejeu automatique de commande ou de secret n'est effectué.

## Validation automatisée et limites

Le test d'intégration Rust crée deux pairs réels, négocie ICE/SDP/DTLS/SRTP sur la machine, transmet simultanément plusieurs unités H.264 Annex-B et plusieurs paquets Opus, puis vérifie cinq paquets RTP reçus sur chaque piste. Les tests de signalisation TLS, d'empreinte invalide et d'anti-rejeu utilisent uniquement la boucle locale. Les sorties FFmpeg sont découpées en unités d'accès H.264 délimitées par AUD et en paquets Opus extraits de pages Ogg ; les files sont bornées, la vidéo obsolète peut être abandonnée et l'audio applique une contre-pression. L'APK est compilé mais jamais installé.

La capture réelle Ubuntu, le décodage matériel Android TV, l'audio/vidéo synchronisé, la stabilité sur plusieurs minutes, D2, la reprise réseau et la latence physique restent `NOT RUN`. Le test local prouve le transport concurrent et chiffré, pas la recette média Ubuntu→TV ni le rendu matériel. Un enfant FFmpeg reste suivi tant que sa récolte n'est pas confirmée et un test force un premier timeout puis une récolte réussie au second essai. Les diagnostics conservent ensemble les échecs média, transport et restauration.

## Recette matérielle à autoriser

Pour chaque Android TV : installer l'APK explicitement autorisé, accepter une nouvelle session, tester D1 puis D2, arrêt et reconnexion. Mesurer par caméra/chronomètre la latence physique médiane, p95 et maximale, FPS, pertes, dérive A/V, CPU/GPU et stabilité. Un RTT WebRTC n'est jamais une mesure bout en bout. Restaurer la topologie d'écran et confirmer l'absence de processus, socket ou session restante.

### Préconditions et preuves

Chaque ligne ci-dessous exige une autorisation explicite au moment de l'essai. Avant D2, enregistrer sous `.ai-private/` la sortie expurgée de `xrandr --query` et `xrandr --listmonitors`. Utiliser une mire animée sans donnée privée et des impulsions audio identifiables. Conserver uniquement états, durées, compteurs, codes de sortie et types d'erreur : jamais adresse, identifiant d'appareil, SDP/ICE, jeton `ctv1`, empreinte, nom de TV ou titre média.

Après chaque essai, vérifier avec `pgrep -a ffmpeg`, `ss -ltnp` et les deux lectures `xrandr` qu'aucun processus, listener ou écran `control-tv-` ne subsiste. En cas de restauration D2 échouée, ne pas lancer une seconde session : utiliser d'abord Stop pour retenter la ressource encore suivie, puis restaurer manuellement la topologie initiale selon la procédure D2 autorisée.

### Matrice reproductible

| Scénario | Action autorisée proposée | Réussite observable | Échec / restauration |
|---|---|---|---|
| D1 WebRTC nominal | Sélectionner `WEBRTC`, dupliquer la mire avec audio pendant 10 min, puis Stop | paquet exact et champ sécurisé confirmés ; H.264 et Opus décodés simultanément ; état final inactif | aucun repli HLS silencieux ; aucun FFmpeg, pair ou bail restant |
| D2 WebRTC nominal | Relever la topologie, étendre, déplacer la mire sur D2, tenir 10 min, puis Stop | seule la source D2 est rendue, géométrie correcte, bureau principal utilisable | topologie strictement identique avant/après ; Stop retentable si restauration refusée |
| AUTO | Répéter avec récepteur+Remote disponibles, paquet absent, inventaire inaccessible, puis Remote indisponible | WebRTC uniquement dans le premier cas ; HLS dans les autres cas compatibles | aucun secret saisi si application ou champ non confirmé |
| Arrêt pendant négociation | Retarder/refuser la réponse du récepteur puis demander Stop | attente annulée dans la borne, état inactif, second démarrage possible | aucun démarrage média tardif ; D1/D2 libéré |
| FFmpeg interrompu | Identifier en preuve privée l'enfant vidéo puis audio et envoyer une fois `kill -TERM PID` | fin détectée, état d'échec compréhensible | autre enfant, transport et bail nettoyés ; aucun zombie |
| Récepteur ou réseau perdu | Couper manuellement le réseau du récepteur pendant un flux stable, puis le rétablir | échec borné, aucun rejeu automatique | reconnexion automatique actuellement non attendue ; Stop puis redémarrage manuel réussi |
| Authentification et rejeu | Présenter sous harnais autorisé une empreinte, un jeton, puis une enveloppe consommée incorrects | aucun SDP/média, refus borné ; ancien secret inutilisable | nouvelle session et nouveau secret requis ; journaux expurgés |
| Endurance A/V | D1 puis D2, 30 min chacun, mire et tops audio, charge normale puis soutenue | FPS/résolution stables, pas de corruption ni coupure persistante | relever dérive A/V au début, 10 min et 30 min, croissance CPU/GPU/mémoire et pertes |
| Sessions successives | Trois cycles Start/Stop par transport | chaque cycle repart proprement, aucune double libération | aucune ressource du cycle précédent et aucune commande rejouée |

La latence se mesure en filmant simultanément la mire source et le téléviseur, puis en calculant médiane, p95 et maximum. La synchronisation A/V se mesure sur les tops visibles/audibles et leur dérive dans le temps. Un succès de CI, un paquet RTP reçu ou un RTT ne remplace jamais ces observations physiques.
