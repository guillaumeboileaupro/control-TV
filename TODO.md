# control-TV — TODO

## Diffusion d'écran — D1, D2, D4 (état consolidé après fusion de #120, PR #121)

Ce tableau remplace, pour la diffusion d'écran, les états partiels plus bas (conservés comme
historique). Le moteur Rust WebRTC, le récepteur Android TV et le bail Python D1/D2 sont dans
`main` depuis #111 (`88f08d3`). Aucune recette matérielle WebRTC n'a été exécutée : les recettes
du 2026-10-26 et du 2026-10-30 ont laissé la diffusion `NOT RUN`. Les seules preuves matérielles
de diffusion sont les essais HLS des 2026-10-19/20 (latence 9–11 s).

Légende : **Fusionné** = dans `main` ; **Branche** = PR #121 non fusionnée ; **Auto** = tests
automatisés locaux/CI ; **Matériel** =
essai réel autorisé par le propriétaire.

| Réf. | Fonction | Code | Auto | Matériel |
|---|---|---|---|---|
| D1-1 | Capture X11 de la géométrie fournie par le bail Python (`x11grab`, mise à l'échelle séparée) | Fusionné | Oui | HLS seulement ; WebRTC non |
| D1-2 | H.264 logiciel `libx264` zerolatency baseline, AUD, GOP = 1 s | Fusionné | Oui | Non (WebRTC) |
| D1-3 | H.264 matériel VA-API avec repli logiciel explicite dans le moteur Rust | Fusionné via #120 | Oui (faux `ffmpeg` : réussite, arrêt, silence, image partielle ; détection du nœud) | Non (GPU non utilisé) |
| D1-4 | Audio Opus depuis la source monitor PulseAudio du bail | Fusionné | Oui | Non |
| D1-5 | WebRTC ICE/DTLS/SRTP, signalisation TLS à jeton unique, LAN uniquement | Fusionné via #120 | Oui (deux pairs locaux, décodage FFmpeg ; scan, mauvais jeton, connexion muette) | Non |
| D1-6 | Récepteur Android TV Kotlin/libwebrtc (écrans d'état, générations, fermeture) | Fusionné | Tests JVM, APK debug CI | Non installé |
| D1-7 | Start/Stop Tauri, erreurs structurées, choix Auto/WebRTC/HLS | Fusionné | Oui | Non |
| D1-8 | Pertes sous retard réseau : horloge RTP conservée, vidéo resynchronisée sur l'IDR suivante, compteurs de pertes réels | **Branche** | Oui (moteur) | Non |
| D1-9 | Latence, FPS, pertes, synchro A/V, CPU sur les deux Android TV | Non mesuré | — | **À faire, avec accord** |
| D1-10 | Audio Opus livré en temps réel (pages Ogg de 20 ms, vidage immédiat ; avant : rien pendant 2 s) | Fusionné via #120 | Oui (vrai FFmpeg, source synthétique) | Non |
| D1-11 | Chaîne Rust complète : contrôleur, appairage TLS, WebRTC, source FFmpeg de production → H.264 (IDR) et Opus reçus, FFmpeg arrêtés au Stop | Fusionné via #120 | Oui (test de bout en bout, entrées synthétiques) | Non |
| D1-12 | Tauri : reconnexion réservée aux pannes de lien ; plus de panique après un échec de `start_media` | Fusionné via #120 | Oui | Non |
| D2-1 | Écran étendu : mode `cvt` sur un connecteur libre « forcé connecté », placé à gauche/droite/dessus/dessous | Fusionné | Oui (faux `xrandr`) | 1 essai HLS (fenêtre déplacée, 2026-10-20) |
| D2-2 | Écran virtuel X11 **indépendant** de tout connecteur, sans action root à chaque démarrage | **Écarté par l'architecture D2 du 2026-11-01** : aucun evdi ni nouveau système ; maintien du connecteur RandR forcé | — | — |
| D2-3 | Diffuser uniquement l'écran étendu (géométrie du bail = cet écran) | Fusionné | Oui | Non (WebRTC) |
| D2-4 | Déplacement des fenêtres sur l'écran étendu | Dépend de GNOME | — | 1 observation HLS |
| D2-5 | Restauration exacte de la configuration initiale après Stop/échec | **PR #121** : retrait idempotent, journal privé atomique, reprise dès le démarrage du bridge, réglages du propriétaire conservés et diffusion vivante protégée | Oui (faux `xrandr`, reprise après plantage simulé, verrou libéré par un vrai SIGKILL) | Partiel sans TV |
| D4-1 | Arrêt fiable : Stop annule attente/négociation/reconnexion, FFmpeg récolté, erreurs combinées | Fusionné | Oui | Non |
| D4-2 | Reconnexion bornée : au plus 2 essais dans l'échéance absolue, transport/jeton/port neufs | Fusionné | Oui | Non |
| D4-3 | Nettoyage FFmpeg : handle conservé tant que la récolte n'est pas confirmée | Fusionné | Oui | Non |
| D4-4 | FFmpeg orphelin après arrêt brutal de l'hôte (SIGKILL) | **Branche** (`PR_SET_PDEATHSIG`) | Oui (processus réels ; le test échoue sans le correctif) | Non |
| D4-5 | Restauration après panne : écran étendu, positions déplacées et route PulseAudio restaurés au démarrage suivant | **PR #121** : X11 et son intégrés avec journal partagé | Oui (faux `xrandr`, faux `pactl`, vrai SIGKILL d'un processus) | Partiel séparé, sans TV |
| D4-6 | Aucune session fantôme : un seul bail émetteur, générations côté récepteur | Fusionné | Oui | Non |

### D1 — duplication (PR D1, après clôture de #118)

- [x] Moteur, récepteur, bail et intégration Tauri (D1-1, D1-2, D1-4 à D1-7) : fusionnés via #111.
- [x] **D1-8 (#120 fusionnée) :** pertes sous retard sans corruption ni dérive.
- [x] **D1-3 (branche, automatisé) :** `VideoEncoder::Vaapi { render_node, qp }` (nœud
  `/dev/dri/renderDNNN`, QP 1–51 validés) produit `-vaapi_device`, `format=nv12,hwupload`,
  `h264_vaapi` en QP constant, profil `constrained_baseline`, sans débit cible (mode qui échoue sur
  l'Intel mesuré). Sans première image complète dans les 3 s (processus arrêté, silencieux ou image
  partielle), le processus VA-API est récolté puis `libx264` démarre ; `video_encoder` et
  `encoder_fallback` le disent dans les diagnostics. Un FFmpeg introuvable reste une erreur, jamais
  un repli. Tauri garde `Software` explicitement.
- [x] **D1-3, intégration (PR #120) :** Tauri prend `VideoEncoder::preferred("/dev/dri")` : VA-API
  (QP 24, mesuré par D0) sur le premier nœud de rendu, sinon `libx264`. Seul le répertoire est lu ;
  un encodage GPU impossible bascule sur `libx264` au démarrage, motif dans les diagnostics.
- [ ] **D1-3, preuve :** vérifier sur matériel, avec autorisation, que le flux VA-API est décodé par
  les deux Android TV (sinon le repli ne s'applique pas : il ne couvre que l'encodeur).
- [x] **D1-10 (PR #120) :** les pages Ogg d'une seconde et le tampon de 32 Kio retenaient l'audio
  (mesuré : aucun paquet Opus en 2 s, puis des rafales), d'où un son en retard sur l'image.
  `-page_duration 20000` et `-flush_packets 1` (aussi pour H.264) ; test avec le vrai FFmpeg qui
  échoue sur les anciens arguments (« arrived in a burst »).
- [x] **D1-5, signalisation (PR #120) :** la première connexion TCP n'est plus la seule : un scan,
  un client sans TLS, un mauvais jeton ou une connexion muette (5 s maximum, 8 en parallèle) sont
  fermés sans annuler la session ; le jeton reste à usage unique une fois présenté.
- [x] **D1-12 (PR #120) :** une panne FFmpeg, de capture, de nettoyage ou un refus du récepteur
  termine la session avec sa cause au lieu de relancer le récepteur ; seules les pannes de
  transport et l'expiration de négociation consomment le budget de 2 reconnexions. Un échec de
  `start_media` ne reboucle plus sur un serveur de signalisation consommé (panique).
- [x] **D1-11 (PR #120) :** test de bout en bout de la chaîne Rust (voir tableau) ; le récepteur
  Android lui-même reste couvert par ses tests JVM et doit être validé sur TV.
- [ ] **Latence résiduelle connue :** une image n'est émise qu'à l'AUD suivante (une image de retard,
  33 ms à 30 i/s) ; à mesurer avec D1-9 avant toute optimisation.
- [x] **D4-4 (branche, automatisé) :** chaque enfant FFmpeg reçoit `PR_SET_PDEATHSIG` = SIGKILL
  (et s'arrête si l'hôte est déjà mort au moment du `fork`) ; un vrai hôte tué par SIGKILL ne
  laisse plus son enfant vivant. Contrainte : lancer depuis un thread de travail Tokio, jamais
  depuis `spawn_blocking` (le signal suit la fin du thread lanceur).
- [ ] **D1-9 :** recette matérielle WebRTC sur les deux Android TV (autorisation requise).
- **Dépendances :** #118, #119 et #120 fusionnées ; dérogations WebRTC valides.
- **Critères d'acceptation :** démarrage/arrêt répétés sans processus FFmpeg, port ni jeton
  restant ; erreurs visibles et distinctes (récepteur absent, négociation expirée, FFmpeg arrêté) ;
  pertes comptées et image figée (jamais corrompue) jusqu'à l'IDR suivante ; sur matériel : latence
  médiane < 500 ms, p95 et pire cas consignés, synchro A/V, 10 min sans dérive.

### D2 — extension (PR D2, après la PR D1)

- [x] Extension sur connecteur forcé, retrait reprenable, cliché du gestionnaire d'affichage (D2-1,
  D2-3, D2-5) : fusionnés.
- [x] **D2-B1 — décision propriétaire du 2026-11-01 :** conserver le gestionnaire X11 et le
  connecteur RandR forcé existants ; aucun `evdi` ni nouveau système d'affichage. GNOME traite ce
  connecteur comme un véritable second espace de travail, distinct de l'écran principal.
- [x] **D4-5, partie X11 (branche D2) :** journal privé atomique écrit et synchronisé avant la
  première mutation RandR ; au prochain processus, retrait du mode, extinction de la sortie et
  remise en place des écrans déplacés ; journal conservé si la vérification échoue et effacé
  seulement après restauration vérifiée. Tests automatisés sur faux `xrandr`, matériel NOT RUN.
- [x] **D4-5, partie son (intégrée dans la PR #121) :** le
  journal atomique de D2 est extrait dans `restore_journal.py` (0700/0600, `fsync`, renommage
  atomique) et partagé par D2 et la route PulseAudio. La sortie par défaut est journalisée avant
  tout `load-module` ; le journal n'est effacé qu'après constat de PulseAudio (plus de puits nul
  `controltv_tv`, sortie par défaut différente). Après un SIGKILL, le bridge termine la
  restauration à son démarrage suivant (`main`), et un nouveau `start` le fait aussi ; avant, le
  puits nul restait la sortie (ordinateur muet) et la diffusion suivante était refusée. La
  restauration ne remet la sortie que si le puits nul est encore la sortie par défaut, ne déplace
  que les sons restés sur lui, laisse PulseAudio choisir si la sortie d'origine a disparu, et ne
  change plus rien une fois faite ; un journal invalide ou une restauration non confirmée est
  conservé et signalé (stderr au démarrage, erreur au `start`). Les tests utilisent désormais un
  `XDG_STATE_HOME` temporaire : aucun test ne peut lire un vrai journal et rejouer `pactl` ou
  `xrandr` sur le poste. Tests automatisés uniquement ; matériel NOT RUN.
- [x] **D2/D4-5, restauration X11 au démarrage (intégrée dans la PR #121) :** le bridge termine
  la restauration de l'écran étendu d'un processus tué dès son
  démarrage, avant le son, sans attendre une nouvelle diffusion ; l'une n'empêche jamais l'autre et
  un échec est signalé sur stderr avec journal conservé et nouvelle tentative au démarrage suivant.
  La reprise ne défait que ce qui appartient encore au processus tué : un écran replacé par le
  propriétaire depuis reste où il est, une sortie qui n'affiche plus le mode TV n'est pas éteinte,
  un écran débranché ne bloque plus le journal ; le journal enregistre le décalage appliqué (un
  journal plus ancien sans décalage garde la remise en place complète). Le journal partagé
  (`restore_journal.py`) porte désormais un verrou `flock` du propriétaire, libéré par le noyau au
  SIGKILL : une reprise (démarrage, autre processus, nouvel objet du contrôleur) ne défait jamais
  une diffusion vivante. Défauts corrigés : chaque `extension_output()` du contrôleur recréait un
  moniteur dont la reprise démontait l'écran étendu en cours ; une extension refusée nettoyait
  l'écran TV d'une autre diffusion ; une reprise hors session X11 levait une erreur non gérée au
  démarrage. Tests automatisés uniquement ; matériel NOT RUN.
- [ ] **D4-5, preuve matérielle (autorisation) :** tuer l'application pendant une diffusion avec
  son, relancer, vérifier que le son revient sur la sortie d'origine. **HARDWARE PARTIAL
  (2026-11-01, autorisé, `e6e5b82`) :** sur le poste du propriétaire, sans TV ni application
  Tauri : route PulseAudio réelle ouverte par un processus, bridge relancé pendant la diffusion
  (route laissée intacte), SIGKILL (ordinateur muet, journal présent), puis démarrage du vrai
  bridge : sortie par défaut, puits, placement des sons et modules identiques à l'état initial,
  journal effacé. Reste : le même scénario avec l'application et une diffusion vers la TV.
- [ ] **D2/D4-5, preuve matérielle X11 (autorisation) :** tuer l'application pendant une
  extension (TV à gauche), relancer sans diffuser, comparer `xrandr --verbose` à l'état initial.
  **HARDWARE PARTIAL (2026-11-01, autorisé)** sur le poste du propriétaire, connecteur HDMI forcé
  par le propriétaire, sans TV ni application Tauri : extension réelle (TV à gauche, écran
  intégré déplacé), bridge relancé pendant l'extension (laissée intacte), SIGKILL, puis démarrage
  du vrai bridge sans diffusion. Premier passage **FAIL** : GNOME avait allumé seul le connecteur
  forcé (1024x768 à droite) et la restauration l'éteignait (disposition remise à la main
  aussitôt). Corrigé : le journal enregistre ce que la sortie affichait (mode, fréquence,
  position, rotation) et l'arrêt normal, l'échec de création et la reprise le lui rendent au lieu
  de l'éteindre. Second passage : `xrandr --verbose` identique à l'état initial hors horodatages
  X, aucun mode `control-tv-*`, journal effacé. Reste : le même scénario avec l'application et
  une diffusion vers la TV, et un arrêt normal sur matériel.
- [x] Le moteur D1/WebRTC consomme exclusivement la géométrie de l'écran étendu via le bail Python ;
  Étendre et Arrêter sont raccordés à Tauri. Validation automatisée existante et ciblée ; recette
  réelle et comparaison `xrandr --verbose` avant/après encore requises avec autorisation.
- **Dépendances :** PR D1 fusionnée ; autorisation pour toute modification d'affichage réelle.
- **Critères d'acceptation :** seul l'écran étendu est transmis ; une fenêtre y est déplaçable ;
  après Stop, échec, Ctrl-C ou SIGKILL puis relance, la topologie, les modes et la route audio
  sont identiques à l'état initial ; aucun mode `control-tv-*` restant.

### Outils en ligne de commande

- [x] **`python3 scripts/agent_config.py check` (branche) :** plantait avec `AttributeError` sur le
  `python3` système 3.7, alors que l'outil est documenté pour lui : `Path.readlink` et
  `Path.is_relative_to` n'existent qu'à partir de 3.9. Remplacés par `os.readlink` et
  `resolved.parents` ; test sans ces API et test de la commande réelle (code de sortie, aucune
  trace). Vérifié avec Python 3.7 et 3.10 ; `dev.py check`, `version-check`, `type-rules`,
  `disk-usage` passent aussi sous 3.7 et `d1 --help` sous `.venv`.

### Blocages

- [x] **D1-B1 levé :** #118, #119 et #120 fusionnées ; `main` intégré dans la branche #121.
- **D1-B2 :** dérogations RUSTSEC-2026-0387/RUSTSEC-2025-0141 limitées au réseau local et à leur
  date d'expiration ; `webrtc` 0.21 conserve `digest` 0.10 via `rtc-crypto` et change l'API de transport.
- **D2-B1 :** résolu par la décision du 2026-11-01 de conserver le connecteur forcé existant.
- **Matériel :** aucune recette D1/D2/D4 sans autorisation explicite du propriétaire.

- [x] **Sécurité Python — pyasn1 0.6.5 (2026-10-31) :** le verrou remplace `pyasn1` 0.6.4,
  transitif via `adb-shell` et `rsa`, par la première version corrigeant CVE-2026-119194,
  CVE-2026-119195, CVE-2026-119196 et CVE-2026-119197. Aucune dérogation ni dépendance
  directe n'est ajoutée. Validation automatisée uniquement, sans interaction matérielle.
- [x] **T-04 — observabilité des commandes (2026-10-26) :** chaque appel Tauri reçoit un identifiant
  transmis au bridge Python et produit des événements JSONL structurés (réception, remise au bridge,
  issue, durée, code d'erreur ou timeout) dans un journal privé, persistant, borné et écrit hors du
  thread de l'interface. Aucun paramètre, appareil, texte, code d'appairage ou secret n'est journalisé.
  Le comptage `bridge_dispatched` prépare T-05 mais ne prouve pas la réception par la TV. Validation
  automatisée, puis recette matérielle ciblée du 2026-10-30 : corrélation et absence de données
  sensibles rapportées PASS. Ce résultat ne prouve pas l'effet physique des commandes.
- [x] **T-05 — comptage corrélé des commandes (2026-10-27) :** le journal T-04 distingue désormais
  une commande utilisateur émise, une réponse positive du transport vers la TV et une confirmation
  issue de l'état relu sur la TV. Chaque événement conserve l'identifiant unique de l'appui et les
  compteurs cumulés émis/transmis/confirmés. Une seconde transmission portant le même identifiant est
  signalée ; deux appuis intentionnels restent distincts. Un résultat incertain est journalisé sans
  être rejoué. Détection bornée aux 1 024 transmissions récentes. Validation automatisée uniquement ;
  recette matérielle ciblée du 2026-10-30 : compteur de doublons à 0 sur le scénario essayé.
  La confirmation physique reste hors de portée du logiciel.
- [x] **Sécurité Rust — GHSA-qwgh-2vcv-g2f7 (2026-10-27) :** les deux lockfiles ne
  contiennent plus `block-buffer` 0.10.4. `digest` 0.10.7 utilise désormais une source locale
  identique à l'amont hors manifeste, raccordée à `control-tv-block-buffer-compat` : API 0.10,
  curseur remis à zéro avant le callback et aucune branche `unreachable_unchecked` sur un état
  corrompu. Le test de régression intercepte une panique puis réutilise le buffer. Aucun avis,
  contrôle ou seuil n'est ignoré. Remplacer ce raccord local quand tout le graphe Tauri/WebRTC
  accepte la génération RustCrypto avec `block-buffer` 0.12.1 ou ultérieur.
- [x] **T-03 — déblocage de la télécommande après Assistant (2026-10-26) :** chaque requête
  télécommande de la fenêtre possède désormais une échéance UI de 25 s, légèrement supérieure à
  la borne Tauri de 20 s. Une réponse hôte perdue devient un `bridge_timeout` visible, libère l'état
  occupé et ne renvoie jamais la commande. Tests automatisés, puis recette matérielle ciblée du
  2026-10-30 : interface Ubuntu restée utilisable après deux clics Assistant (PASS).
- [ ] **T-06 — découverte persistante (FAIL matériel P1, 2026-10-30) :** une TV éveillée et
  annoncée sur le réseau est restée absente de trois découvertes successives sur Ubuntu et le
  téléphone. Garder une TV déjà appairée visible et reconnectable reste à concevoir et tester ;
  aucun correctif n'est encore implémenté.
- [x] **Sécurité Rust — RUSTSEC-2026-0386 :** le lockfile Tauri résout désormais Tokio 1.53.3,
  premier correctif compatible de la ligne LTS 1.53. Les audits restent actifs et aucune
  dérogation n'a été ajoutée. Validation automatisée uniquement, sans interaction matérielle.
- [x] **Sécurité Python — GHSA-xmj3-9gf7-h52w (2026-10-24) :** `setuptools` 84.0.0 → 84.1.0
  (groupe `android`, backend des roues embarquées) ; l'audit Python échouait sur toutes les branches.
  Aucune dérogation. Validation automatisée uniquement.
- [x] **Nettoyage D1/D2 (2026-10-24, défauts signalés sur #97) :** un retrait D2 dont une étape
  tardive échoue reprend après les étapes déjà faites (le bureau est remis en place) ; l'arrêt D1
  retire l'écran virtuel même après `KeyboardInterrupt`, `SystemExit` ou une erreur imprévue.
  Validation automatisée uniquement ; à reporter dans #97/#106 par leur auteur.
- [x] **CI (2026-10-25) :** tests `ping`/`tvApps` indépendants de la présence d'`adb` sur la machine ;
  tests d'interaction du pavé directionnel (5 zones câblées, disjointes, symétriques). Aucun défaut
  de zone reproduit (Chromium et WebKitGTK). Validation automatisée uniquement.
- [x] **Configuration agents (2026-10-25) :** le skill `repository-lifecycle` ajouté sur `main` (`baf9f68`)
  n'avait pas son lien `.claude/skills/` ; lien ajouté, `agent_config.py check` revient à 0 erreur.
- [x] **Artefacts CI :** l'APK de debug du récepteur Android TV est publié avec le `.deb` et l'APK
  télécommande.
- [x] **Dérogations WebRTC — risque accepté par le propriétaire le 2026-10-25 :** RUSTSEC-2026-0387 et
  RUSTSEC-2025-0141 jusqu'au 30/11/2026, essais en réseau local uniquement (voir `SECURITY.md`).
- [ ] **Avant le 30/11/2026 :** migrer vers `webrtc` 0.21 (pile `rtc`) et retirer les trois dérogations.
- [x] **Applis TV sur le téléphone (2026-10-25) :** #99 ne gardait que le programme `adb`, absent du téléphone ;
  l'adaptateur Python `adb-shell` de `main` est rétabli et choisi quand `adb` manque. Récepteur TV : nom,
  icône et bannière, consigne claire à l'écran. Validation automatisée ; essai réel en cours.
- [x] **Son pendant l'écran vers la TV (2026-10-25) :** HLS et WebRTC acquièrent désormais la même
  route PulseAudio temporaire ; le bail WebRTC transmet sa source monitor au moteur Opus et la libère,
  avec reprise du nettoyage si la restauration échoue. Tests automatisés PASS ; lecture audio WebRTC
  sur TV et retour réel des haut-parleurs Ubuntu restent à valider sur matériel.
- [x] **Retard de diffusion (2026-10-25) :** les files FFmpeg HLS de 1 024 entrées (jusqu'à des dizaines
  de secondes de vieilles images) sont bornées à 4 images/8 paquets audio, et le démarrage HLS passe de
  4×2 s à 2×1 s. WebRTC ne bloque plus la lecture FFmpeg quand ses files 1/4 sont pleines : les nouveaux
  paquets sont abandonnés jusqu'au retour du transport, au lieu d'accumuler dans les pipes. Validation
  automatisée uniquement ; la cible bout-en-bout < 500 ms exige encore une mesure réelle sur TV.
- [x] **Télécommande complète (demande du propriétaire, 2026-10-25) :** pavé numérique, Input, Settings, Info,
  Guide, chaînes, sous-titres, piste audio, touches de couleur, touches média et touche Google Assistant
  (ouvre l'Assistant de la TV). Validation automatisée ; essai réel à faire.
- [x] **Fenêtre Ubuntu adaptée à l'ordinateur (demande du propriétaire, 2026-10-25) :** à partir de 960 px, le contexte
  à gauche et la télécommande au centre, avec les applis TV à côté à partir de 1180 px ; fenêtre par défaut 1280×820.
  Le téléphone garde sa colonne unique. Vérifié par capture ; essai réel à faire.
- [x] **Noms et logos des applis TV (demande du propriétaire, 2026-10-25) :** RMC BFM Play, Aktu Free, M6+, OQEE,
  Diagnostic Freebox, ControlTV Récepteur (avec le logo de control-TV) et l'entrée Disney+ de la Freebox (logo Disney+).
  Pas de logo à licence libre pour RMC BFM Play, OQEE, M6+, TFOU MAX, Aktu Free : initiale conservée.
- [x] **Pavé directionnel (2026-10-25) :** quatre quarts d'anneau et OK, sans zone morte ni chevauchement ; grille de
  101×101 points (bords, angles, frontières) testée, et 3 285 points mesurés dans Chromium et WebKitGTK.
- [x] **Récepteur TV (2026-10-25) :** écrans attente/connexion/diffusion/interruption/erreur, plus de session fantôme
  (générations), session fermée à l'arrêt, écran maintenu allumé seulement pendant un flux. 13 tests JVM.
- [x] **Touches de la télécommande (2026-10-25) :** chaque touche testée de bout en bout (requête, attente, issue) et
  chaque code vérifié dans le protocole de la TV (`androidtvremote2`).
- [x] **Tests du bon code (2026-10-25) :** pytest charge le `src/` du worktree en premier, y compris pour les
  processus Python lancés par les tests (`tests/conftest.py`) ; un environnement d'un autre worktree ne fait plus
  tester le mauvais code. Tests de Freebuff (carte des touches) intégrés.
- [x] **Menu GNOME (demande du propriétaire, 2026-10-25) :** menu français synchronisé avec la fenêtre :
  sélection/connexion d'une TV, lecture/pause, arrêt, muet, volume, duplication et arrêt de diffusion. Il affiche
  l'état confirmé de la connexion et de la diffusion et désactive les actions indisponibles. Validation automatisée ;
  validation visuelle sur le bureau GNOME installé encore requise.
- [ ] **Validation matérielle (2026-10-25) :** à faire avec le propriétaire selon `docs/QUICK_ACCEPTANCE.md`
  (Ubuntu, téléphone, Liberté boîte, Chromecast) ; aucune fonctionnalité n'est déclarée validée par la seule CI.
- [ ] **Micro (décision du propriétaire, 2026-10-25, remplace « pas de micro ») :** envoyer la voix du téléphone
  ou du PC à l'Assistant de la TV (`androidtvremote2.start_voice`, PCM 16 bits mono 8 kHz).
  Fait côté Android : `VoiceCapture` (capture à la demande, permission, arrêt garanti, 8 tests). Reste : session
  vocale dans l'adaptateur de télécommande et le bridge, puis permission `RECORD_AUDIO` et bouton.
- [x] **Décision propriétaire — RUSTSEC-2026-0387 (`crypto-bigint` 0.5.5 via `webrtc` → `dtls` →
  `p256`/`p384`) :** tranchée le 2026-10-25 par une dérogation datée et ciblée (entrée
  « Dérogations WebRTC » ci-dessus, `webrtc-engine/cargo-audit-waivers.json`, `osv-scanner.toml`) ;
  la migration `webrtc` 0.21 reste ouverte (D1-B2).
- [x] **CI :** `webrtc-engine` exécute fmt, Clippy, tests de reconnexion/décodage et son propre
  audit Cargo ; `android-tv-receiver` exécute ses tests JVM avec verrou Gradle. Un audit OSV
  bloquant couvre les deux verrous sans ajouter un second build Android coûteux.
- [x] **Hygiène de build (2026-10-24) :** `webrtc-engine/target/` et `android-tv-receiver/` ignorés et
  nettoyés par `dev.py clean` ; test d'expiration du jeton d'appairage WebRTC ajouté.
- [x] **Réconciliation #99 :** les protections ADB sans fil/TLS sont conservées sur la base D1/D2
  réconciliée et le paquet Debian déclare ensemble `adb`, `ffmpeg`,
  `libayatana-appindicator3-1` et `x11-xserver-utils`. Validation automatisée uniquement.

## Priorité — moteur Rust WebRTC à faible latence et récepteur Android TV (décision propriétaire, 2026-10-21)

**Objectif :** remplacer, pour D1 (duplication) et D2 (extension), la latence HLS mesurée de 9 à 11 secondes par un flux interactif. Cible de recette : latence bout-en-bout < 500 ms ; objectif ambitieux 150–250 ms, à mesurer sur matériel et non à présenter comme acquis. Les deux équipements de réception sont traités uniformément comme **Android TV**, sans branchement produit ou marque dans l'architecture.

- [x] **Frontière frontend vérifiée par Graphify :** les cycles réels `model.ts` ↔ `remote.ts`
  et `model.ts` ↔ `playback.ts` ont été supprimés en déplaçant uniquement leurs formes d'état
  partagées dans le module feuille `view-state.ts`. Un test fondé sur l'AST TypeScript vérifie
  désormais tout le graphe des modules de production, son absence de cycle, la frontière du
  modèle racine et le caractère feuille de `view-state.ts`. Validation automatisée uniquement.

**Architecture décidée :** conserver l'interface Tauri 2 et le `ControlService` Python/PyChromecast pour découverte et commandes ; créer un **moteur émetteur Rust** pour sessions, capture D1/D2, audio, encodage et transport WebRTC (RTP/RTCP/ICE) ; installer avec accord du propriétaire une **application réceptrice Android TV** avec décodage matériel et rendu plein écran (Kotlin autorisé pour l'intégration Android). Ne pas prétendre que le récepteur Cast/HLS natif sait recevoir WebRTC. Conserver HLS en repli temporaire, avec modes WebRTC / HLS / Auto sélectionnés selon les capacités réellement vérifiées. Aucun serveur permanent ni exposition Internet ; réseau local, authentification et consentement explicite.

- [x] **Conception :** ports typés, cycle de vie et choix `webrtc-rs` 0.14 / libwebrtc Android documentés dans `docs/WEBRTC_STREAMING.md` ; les mesures matérielles restent séparées.
- [ ] **Émetteur Rust :** prototype isolé D1, capture écran + audio système, H.264 temps réel avec accélération matérielle si disponible et repli explicite ; contrôle des buffers, horodatage A/V, arrêt propre, reconnexion bornée et absence de rejeu des commandes TV.
- [ ] **Récepteur Android TV :** APK installable avec consentement, signalisation WebRTC locale, décodage matériel, audio synchronisé, affichage plein écran, diagnostics et fermeture/nettoyage sûrs ; vérifier la compatibilité sur les **deux appareils Android TV**.
- [ ] **D2 :** brancher le même moteur Rust sur l'écran étendu et le gestionnaire d'affichage de #98 ; conserver et restaurer exactement la configuration d'écrans après arrêt/erreur.
- [ ] **Intégration Tauri :** commandes D1/D2/Stop, états de session, choix Auto/WebRTC/HLS, erreurs et capacités explicites ; préserver les protections D4 (consentement, une session, durée bornée, LAN).
- [ ] **Tests automatisés :** unitaires, intégration émetteur/récepteur simulé, pertes réseau, retard, dérive audio, changement de résolution, reconnexion, nettoyage, sécurité ; CI adaptée aux plateformes sans revendiquer de validation matérielle.
- [ ] **Recette matérielle autorisée :** mesurer la latence bout-en-bout (médiane, p95, pire), FPS, pertes d'images, synchronisation audio, CPU/GPU, stabilité et reconnexion en D1 et D2 sur chacun des deux Android TV ; consigner les protocoles, builds et preuves. Critère < 500 ms uniquement si effectivement mesuré.
- [ ] **PR et dépendances :** préserver les acquis #97 (capture, son, D1/D2) et #98 (gestionnaire d'affichage) ; #98 dépend de #97. Ne pas fusionner de code non revu : Codex implémente sur branche dédiée, revue indépendante, CI et autorisation du propriétaire avant fusion. Ne pas modifier les PR existantes sans revue de leur état.
- [ ] **Mise à jour documentaire après preuves :** réconcilier TODO, DEVELOPMENT_PLAN, checklist et README, distinguer simulation/CI/matériel. Aucun délai ou budget horaire dans TODO.

**État initial :** chantier décidé mais **non implémenté ni validé** en Rust/WebRTC ; D1 HLS a montré 9–11 s sur matériel dans #97 ; la fluidité D2 et le parcours complet depuis la fenêtre restent à valider. Les valeurs WebRTC annoncées sont des objectifs, pas des résultats.

**État logiciel du 2026-10-21 (branche `feat/rust-webrtc-streaming`) :** moteur Rust, capture/encodage FFmpeg X11 + PulseAudio, transport H.264/Opus WebRTC, signalisation locale TLS authentifiée à usage unique, commandes Tauri dédiées et récepteur Android TV compilable sont implémentés. Le certificat éphémère de chaque session est épinglé par empreinte SHA-256 côté Android ; le jeton client reste dans le canal chiffré et l'écoute disparaît après un échange. Les sorties FFmpeg sont maintenant découpées en unités d'accès H.264 (AUD, images clés) et paquets Opus Ogg horodatés, avec files bornées, vidéo obsolète éliminée sans accumuler de latence, audio soumis à contre-pression et pompage concurrent. L'arrêt média coopératif garantit le nettoyage de FFmpeg. Un test d'intégration local a réellement négocié ICE/DTLS/SRTP et reçu du RTP H.264. L'interface graphique Auto/WebRTC/HLS, la découverte de capacité du récepteur, les scénarios réseau/reconnexion, D2 complet et toute recette matérielle restent ouverts ; aucun objectif de latence n'est déclaré atteint.

**Robustesse logicielle :** Stop annule l'attente, la négociation ou la reconnexion avant fermeture du transport ; une fin FFmpeg prématurée devient un état d'échec explicite et déclenche le nettoyage. Une perte WebRTC persistante pendant la diffusion arrête et récolte FFmpeg, ferme le transport, puis reprend au plus deux fois dans l'échéance absolue de la session avec un pair, un certificat et un jeton neufs. Le bail D1/D2 reste détenu entre les essais et la commande TV n'est pas rejouée ; Stop attend l'annulation de la tâche avant de restaurer le bail.

**Nettoyage renforcé (2026-10-24) :** un enfant FFmpeg n'est plus retiré du suivi avant confirmation de sa récolte ; un timeout ou une erreur conserve le handle pour une nouvelle tentative. Les erreurs simultanées du pipeline média, du transport WebRTC et de la restauration D1/D2 sont toutes conservées dans le diagnostic. Une négociation sans réponse ou une perte persistante en streaming est retentée au plus deux fois, avec un nouveau transport, certificat, port et jeton ; un watchdog récepteur de 20 s rouvre le champ sécurisé avant le timeout hôte de 30 s, et l'état demeure visible et annulable par Stop pendant l'attente entre essais. Tests automatisés uniquement ; la preuve d'absence de processus orphelin sur la plateforme réelle reste à faire.

**Pipeline simulé :** deux pairs WebRTC locaux négocient ICE/DTLS/SRTP, transmettent simultanément plusieurs échantillons H.264 et les paquets Opus bruts produits par le framing de production, les dépaquetisent côté réception, remuxent seulement l'Opus reçu puis font réellement décoder les deux flux par FFmpeg. Une épreuve synthétique séparée maintient pendant deux minutes les pistes vidéo/audio, force et vérifie les maxima de files 2/16 et borne l'écart des chronologies média. Ces preuves ne valent pas décodage Android matériel, synchronisation audible/visible ni validation TV.

**Revue indépendante du 2026-10-21 :** les blocages sur le nettoyage après échec partiel, la conservation conjointe des erreurs média/nettoyage, l'échéance d'une piste silencieuse, la réponse Android après ICE complet et la documentation obsolète ont été corrigés après les verdicts `CHANGES REQUESTED`. La revue finale du SHA `acefef9` est `APPROVED`, sans P0/P1/P2/P3 nouveau ; elle reste strictement logicielle. Les écarts fonctionnels UI/capacités, perte réseau/reconnexion et D2 restent ouverts.

**Détection logicielle du récepteur (2026-10-22) :** le pont expose maintenant les capacités du seul équipement sélectionné. Une lecture d'état Cast réussie confirme équipement joignable et repli HLS ; l'inventaire d'applications existant confirme séparément l'installation et l'accessibilité du paquet exact `ControlTV Receiver`, puis une lecture Android Remote confirme le canal appairé requis pour le transfert secret. Paquet absent, inventaire ADB inaccessible, canal Remote indisponible et équipement indisponible restent des états distincts ; aucune compatibilité WebRTC n'est déduite du seul type Android TV. Cette capacité alimente le sélecteur AUTO/WEBRTC/HLS.

**Bail de capture WebRTC (2026-10-22) :** le contrôleur D1/D2 Python existant prépare désormais une géométrie X11 pour le moteur Rust, après revalidation du paquet récepteur exact. En duplication il réserve l'écran sélectionné ; en extension il utilise le `XrandrVirtualMonitor` existant et le libère sur arrêt ou fermeture du bridge. Le moteur Rust distingue maintenant la taille de la source de la taille encodée et applique une mise à l'échelle explicite. Le sélecteur graphique reste ouvert.

**Robustesse du bail (2026-10-22) :** une fermeture concurrente annule maintenant une préparation WebRTC avant publication du bail et attend son nettoyage borné. Une restauration D2 échouée conserve la ressource pour une nouvelle tentative, et le diagnostic réunit l'erreur moteur et l'erreur de restauration au lieu d'en masquer une. Tests automatisés sur fakes uniquement ; l'orchestration Tauri complète reste ouverte.

**Transaction Tauri/Rust (2026-10-22) :** la commande de démarrage WebRTC acquiert désormais elle-même le bail Python autoritatif, consomme sa géométrie source réelle, active le bail seulement après négociation et démarrage média, puis le libère sur erreur, fin de média ou arrêt. La commande ne reçoit plus de coordonnées X11 fournies par l'interface. Le choix AUTO/HLS et le sélecteur graphique restent ouverts.

Le cycle de vie Tauri sérialise maintenant démarrage, arrêt et lecture terminale afin qu'un arrêt concurrent ne puisse pas être dépassé par une publication tardive. Tous les chemins conservent la cause WebRTC initiale et une éventuelle panne du RPC de restauration, sans dupliquer un diagnostic déjà combiné par Python.

**Sélecteur graphique et pairage automatique (2026-10-22) :** la carte « Screen to TV » existante propose Auto, WebRTC et HLS. Auto lit les capacités réelles du téléviseur sélectionné et préfère WebRTC uniquement lorsque le récepteur exact et le canal Remote appairé sont confirmés, sinon HLS ; WebRTC explicite échoue sans repli silencieux. L'état indique le transport effectivement utilisé et une génération d'opération empêche une réponse de polling antérieure de dépasser Start/Stop. Le shell exige que le gestionnaire existant confirme le paquet exact au premier plan, puis transmet un paquet éphémère compact uniquement si Android Remote signale un champ actif ; aucun secret ne passe dans ADB, le DOM, la réponse Tauri, une autre application ou les logs. Le récepteur masque, valide et efface le champ avant connexion. Validation automatisée uniquement ; ce transfert et le focus du champ restent à confirmer sur chaque TV.

**Appairage ADB simplifié et inventaire compatible (2026-10-25) :** après une demande
`pairing_required`, le formulaire accepte directement le code temporaire à six chiffres et le
bridge sélectionne l'unique service `_adb-tls-pairing._tcp` annoncé par la TV ; l'adresse reste
un secours facultatif et aucune commande d'appairage n'est rejouée. La liste essaie la requête
Package Manager moderne puis son équivalent historique seulement si la première est absente.
Tests Python/UI uniquement : saisie et validation matérielles restent à effectuer avec le
binaire candidat installé après autorisation.

---

## État des travaux — 2026-10-19

Ce fichier suit uniquement les tâches, leurs dépendances et les preuves d'exécution.
**Aucune estimation de charge ni budget horaire n'est conservé ici : le suivi du temps est exclusivement dans la conversation avec le propriétaire.**
Les validations automatisées et matérielles sont distinctes. Aucun essai TV, GPU, capture, installation ou modification système sans accord explicite.

| Chantier | Travail logiciel livré | Reste à faire / preuve attendue |
|---|---|---|
| D0 — encodage H.264 | Socle et intégration automatisée (#79, #81, #91) ; flux H.264 logiciel réel exécuté via #92 | Validation VA-API/GPU autorisée et intégrité réelle selon protocole ; ne pas confondre avec la réception TV |
| D1 — duplication | Ports et orchestration (#83, #86) ; publisher HLS, récepteur Cast, CLI et capture X11 réelle (#92) ; surveillance de l'arrêt spontané du publisher (#95). **Recette 2026-10-19 (TV réelle)** : bureau affiché, image fluide au 1er essai, latence mesurée 10,3 s ; saccades reproduites quand le récepteur est chargé avant d'avoir assez de segments ; segments de 1 s non lus par le récepteur Cast de la Freebox. Branche `fix/screencast-d1-d2-hardware` : chargement après 4 segments de 2 s, son système (PulseAudio, AAC) et fermeture de la session Cast possédée — tests automatisés verts | **2026-10-20 (TV réelle)** : fluide, son présent et synchronisé ; latence mesurée 9–11 s (plancher HLS du récepteur par défaut). Réduction forte de la latence : autre chemin (recopie Cast ou récepteur dédié). Revalider sur TV les sessions successives et la fermeture du lecteur distant après le correctif #97 |
| D2 — extension | **2026-10-20** : extension sur un connecteur libre forcé « connecté » ; essai réel : fenêtre déplacée sur l'écran étendu et visible sur la TV. PR #97 : topologie X11 réelle, modes relus via `xrandr --verbose`, coordonnées libres, orientation, échelle, écran principal, Joindre/Cloner/Écran unique, application atomique et restauration temporisée — tests automatisés uniquement pour le gestionnaire | Forçage du connecteur par l'utilisateur (`sudo`, une fois par démarrage) ou règle système durable à décider ; gestionnaire, fluidité et restauration à valider dans l'application installée ; aucune nouvelle preuve matérielle pour le gestionnaire |
| D2 — extension | Moniteur virtuel `xrandr` à l'exécution, diffusé puis retiré (#92), validé avec un faux `xrandr` | Moniteur virtuel vu sur le bureau X11 et sur la TV (RD5), avec accord |
| D3 — interface | Carte « Screen to TV » et gestionnaire inspiré de GNOME : Dupliquer / Étendre / Arrêter, écrans numérotés, sélection, glisser-déposer magnétique, réglages réellement annoncés par X11, Appliquer/Annuler/Garder/Rétablir ; relais Tauri et tests unitaires dans #97 | Utilisation du nouveau gestionnaire avec la TV et vérification visuelle/réelle de chaque mode |
| D4 — sécurité | Consentement exigé par le bridge, un flux à la fois, borne d'une heure, arrêt à la fermeture, LAN + jeton ; procédure `docs/SCREENCAST_TV_TEST_PROCEDURE.md` ; #97 conserve le cliché précédent tant que la restauration n'a pas réussi et permet une nouvelle tentative | Vérification RD1–RD8 et de la fermeture distante sur matériel, sans compter deux fois les essais D1 |
| Packaging | Provenance et automatisation (#84) ; dépendances Ubuntu complètes ; mécanisme de signature Android stable prêt | Propriétaire : créer et sauvegarder la clé Android, publier son empreinte, définir les deux secrets CI, puis identifier les artefacts de la première CI signée |
| Recette des télécommandes | Traces C6/C7 (#85), classification K1/K2 (#90), tests automatisés | Essais Ubuntu/Android, R1–R4, C1–C7, K1–K3, tray/logos et rapport sur matériel après accord |

**Statuts d'intégration :** #82, #90, #91, #92, #93 et #95 sont fusionnées.

**Sécurité des dépendances (2026-10-23, branche `fix/deps-audit-certifi-vite`) :** la CI de
`main` (`f0dba1d`) et des PR ouvertes échouait sur deux nouveaux avis : `certifi`
PYSEC-2026-4320 (racines emSign compromises) et `vite` GHSA-587h-7wmj-h5pm (élevé, via
`launch-editor`). Lockfiles mis à jour (`certifi` 2026.10.19, `vite` 8.3.4) ; audits Python et npm,
contrôles Python et UI réussis localement. Validation automatisée uniquement.

**Ordre logiciel :** terminer D1, puis D2, D3 et D4 selon le plan imposé. Les essais sur TV auront lieu uniquement lorsque le propriétaire le décidera.

---

## 0. Suivi de la recette finale et de l'affichage distant — 2026-10-17

**État de référence :** PR #71 (logos) et #72 (recette automatisée) fusionnées dans `main` ; commit `7569c1c`, CI push `39021854559` réussie. Artefacts de cette CI récupérés localement dans `dist/final-acceptance/` : `control-tv_0.1.0_amd64.deb` et `app-universal-debug.apk`. Les anciens artefacts du 15 octobre ne sont pas des preuves pour cette version.

**Constat matériel du 2026-10-17 :** capture de l'interface Ubuntu `control-TV` affichant le média YouTube en cours et la liste des applications de la TV. Les tuiles visibles utilisent des initiales, notamment Apple TV alors qu'un logo est prévu. Des noms techniques `com.*` et des libellés tronqués sont visibles. **Ne pas considérer le rendu des logos comme validé.** La capture seule ne prouve ni la version du binaire installé, ni l'identifiant Android exact renvoyé pour Apple TV, ni la cause de l'absence des SVG.

### A. Traçabilité et environnement

- [x] Fusion #71 et #72 dans `main` ; CI `39021854559` verte.
- [x] Artefacts finaux téléchargés depuis la CI du commit `7569c1c`.
- [x] Environnement Ubuntu X11 identifié (`XDG_SESSION_TYPE=x11`).
- [x] Script de recette lancé avec `.venv/bin/python` (Python 3.12.13) ; `python3` global est Python 3.7.3 et ne dispose pas de `tomllib`.
- [x] Ancien paquet : cinq contrôles Ubuntu statiques/simulés PASS, réel BLOCKED sans autorisation d'installation.
- [x] Contrôles statiques refaits sans installation avec les **artefacts finaux** : cinq PASS Ubuntu (métadonnées/contenu `.deb`, CSP/IPC, tray, simulation IPC), quatre PASS Android (archive/arm64/pont, bridge/widget, CSP, simulation), métadonnées Android SKIP faute de `aapt`, scénarios réels BLOCKED faute d'autorisations. L'installation Ubuntu existante n'a pas été modifiée.
- [x] SHA-256 conservés : `.deb` `52248c8fef03fb1588bf5bce7ab57596814b517212d283de32fb1c293502d039`, APK `8db2e69ce0be597f2bba10ef6c43393ec1c69605fa9d011215105dbff1e31d79` ; les rapports de recette enregistrent le SHA Git exécuté.

- [x] CI (PR #89 fusionnée) : une PR ne lance que les builds de plateforme que ses fichiers peuvent affecter, calculés sur tous les chemins depuis la base de fusion (suppressions et deux côtés d'un renommage compris ; chemin inconnu = tous les builds) ; `main` lance toujours tout ; caches Rust par job et `Cargo.lock`, sans les paquets `.deb`/`.apk`.

### B. Anomalie visuelle : logos et grille d'applications — priorité immédiate

- [x] **ADB sans fil Ubuntu (2026-10-21, validation automatisée)** : serveur ADB existant,
  appareils TCP et TLS, services mDNS, ports dynamiques, reconnexion, refus des correspondances
  absentes/ambiguës et ciblage explicite par `adb -s`. L'interface propose l'association par
  adresse/port et code temporaire, sans conserver le code. Correctifs de revue : une cible TCP
  classique oubliée après redémarrage du serveur reçoit une seule reconnexion bornée sur 5555 ;
  `adb pair` est vérifié avant usage ; l'UI masque les applications quand le bridge annonce
  `tvApps: false` (notamment Android).
- [x] **Correctifs de revue ADB #99 (simulation)** : l'absence de `adb mdns` ne bloque plus une
  cible TCP classique déjà connectée ou reconnectable sur 5555 ; après une association acceptée,
  l'apparition du service de connexion est attendue de façon bornée sans rejouer
  `adb pair` ; la version Platform Tools est analysée avant association (ancienne, invalide,
  bloquée ou défaillante refusée) ; le code à usage unique est masqué, consommé à la soumission et
  effacé si le formulaire ferme ou si la TV change. Un service TLS annoncé qui refuse la connexion
  n'est jamais remplacé par une tentative non-TLS sur 5555. Aucun appareil n'a été contacté.
- [x] **Politique de packaging ADB moderne (audit sans installation)** : conserver `adb` Jammy
  pour TCP classique ; ne pas redistribuer l'archive Google tant que ses conditions SDK n'ont pas
  reçu de validation juridique explicite ; traiter Platform Tools >= 30 comme prérequis externe
  pour l'association TLS. La candidate 37.0.1 est épinglée par URL officielle versionnée, taille
  et SHA-256 dans `packaging/adb-platform-tools.json`, avec procédure de licence et mise à jour
  dans `docs/ADB_PLATFORM_TOOLS.md`. L'archive n'entre ni dans Git ni dans le `.deb`.
- [ ] **Fourniture ADB moderne sur installation propre** : le paquet `adb` d'Ubuntu 22.04 ne
  fournit pas `adb pair`. Tant qu'une source redistribuable validée ou une baseline système plus
  récente n'est pas décidée, ne pas présenter l'association sans fil comme incluse dans le `.deb`.
- [ ] **Recette matérielle restante** : depuis l'interface Ubuntu, relister, actualiser, lancer une
  application, observer son ouverture, puis redémarrer ControlTV et vérifier la réutilisation de
  la connexion. Aucun nouvel essai matériel n'a été exécuté par cette branche.
- [ ] **Android** : définir un chemin TLS sans serveur permanent dans l'application ; le serveur
  ADB platform-tools utilisé ici est disponible sur Ubuntu seulement.

- [x] **Codex — diagnostic sans supposition :** le paquet installé est `control-tv` 0.1.0 amd64, mais son binaire du 15 octobre (`84186c3ea194052587595fa59accf96c012256fc5fc508d3b0661d4ec7140a53`) diffère du binaire extrait sans installation du `.deb` final du 17 octobre (`f4678ae03c13999a180b48442056359ab087b7c989fe34a1b22ed5ddb6e5149f`). La capture utilisait donc un ancien bundle, antérieur à #71. Le bundle frontend reconstruit contient l'identifiant Apple TV, son SVG et l'appel `appLogo`; la CSP reste validée par la recette statique.
- [x] **Codex — correspondances :** le backend n'émet le libellé « Apple TV » que pour l'identifiant exact `com.apple.atve.androidtv.appletv`, ce qui établit l'identifiant reçu dans la capture sans nouvelle connexion TV. Il correspond exactement à `ui/src/app-logos.ts`; aucun alias ou logo approximatif n'a été ajouté. L'identifiant exact de chaque tuile est désormais conservé dans son infobulle et son nom accessible.
- [x] **Codex — UI :** noms connus lisibles, identifiant technique accessible dans l'infobulle et aux technologies d'assistance ; fallback technique inconnu limité à trois lignes, police compacte et césure sans débordement ; logos officiels et fallback par initiale inchangés. Validation matérielle avant/après toujours ouverte.
- [x] **Codex — tests automatisés :** mapping exact, SVG valides, fallback, absence de correspondance approximative et de requête réseau ; présentation du libellé/identifiant testée ; TypeScript strict, UI, bundle (`56 996 / 65 536` octets), Rust, `.deb` Ubuntu et APK Android validés par la CI PR #74 (`39024170712`). Les captures réelles restent exclusivement dans la validation propriétaire ci-dessous.
- [ ] **Claude — revue indépendante :** vérifier cause racine, licences, conformité des logos, qualité visuelle, tests et risques de régression ; rapport READY/BLOCKED ; aucune fusion sans autorisation.
- [ ] **Propriétaire — validation réelle :** après build et installation explicitement autorisée, contrôler visuellement Apple TV et les autres logos réellement pris en charge sur Ubuntu puis Android. Conserver la capture avant/après.

#### B bis. Couverture des logos — 2026-10-17 (branche `feat/more-tv-app-logos`)

Constat après installation du `.deb` de #74 : un seul logo (Apple TV) dans la grille réelle ; ARTE, CANAL+, Disney+ et d'autres en initiale. Cause : Simple Icons 16.33.0 ne contient aucune de ces marques.

- [x] **Couvertes par un fichier officiel embarqué** (`assets/app-logos/`, domaine public ou CC0 sur Wikimedia Commons, servi localement, sans réseau) : ARTE `tv.arte.plus7`, CANAL+ `com.canal.android.canal`, Disney+ `com.disney.disneyplus`, france.tv `fr.francetv.pluzz`, Google Play Games `com.google.android.play.games`, Molotov `com.molotov.app` (tuile sombre, couleurs inchangées), Prime Video `com.amazon.amazonvideo.livingroom`, TF1+ `fr.tf1.mytf1`.
- [x] **Couvertes par Simple Icons** (inchangé) : Apple TV, Google Play Store, Google TV home, MUBI, Netflix, Plex, Spotify, VLC, YouTube, YouTube Kids, YouTube Music.
- [x] **Sans logo, avec raison** : M6+ `fr.m6.m6replay.by` (seul le logo de la chaîne M6 existe : approximation refusée) ; Paramètres `com.android.tv.settings` et Recommandations `com.google.android.tvrecommendations` (les marques Android et Google TV désignent d'autres produits) ; toute application inconnue garde son initiale.
- [x] **Tests** : correspondance exacte identifiant -> logo pour les 19 applications ; catalogue limité exactement à ces 19 ; exclusions et identifiants proches ou hérités (`constructor`, `__proto__`) refusés ; sources limitées au dossier local ; fichiers SVG sans script, contenu étranger, référence externe ou DOCTYPE (`tests/test_app_logo_assets.py`) ; chaque fichier documenté avec sa source et sa licence ; aucune requête réseau. 342 tests UI, 1 573 tests Python ; bundle JavaScript 57 858 / 65 536 octets ; 8 fichiers (44 Ko) livrés avec le frontend ; grille rendue sous la CSP de production : 8 logos fichiers, 11 marques, 0 image cassée, 0 violation.
- [ ] **Propriétaire — contrôle visuel** sur Ubuntu puis Android après installation autorisée, en particulier CANAL+ (dessin de 1995), Disney+ (version 2019) et TF1+ (reproduction CC0).
- [ ] **Restant** : logo M6+ si une source sous licence redistribuable apparaît ; noms et logos des applications réellement installées encore inconnues du catalogue, à relever via ADB (après accord) ; Paramètres et Recommandations restent en initiale par choix.

#### B ter. Applications réellement installées sur la Freebox — inventaire complet (2026-10-17)

La TV est une Freebox sous Android TV ; 28 applications, relevées sur quatre captures de la grille (11 h 57, 13 h 43, 14 h 31, 14 h 32). Noms vérifiés sur la fiche Google Play de chaque identifiant ; aucun identifiant deviné.

| # | Identifiant exact | Nom affiché | Logo | Source / licence | Raison si absent |
|---|---|---|---|---|---|
| 1 | `com.apple.atve.androidtv.appletv` | Apple TV | oui | Simple Icons, CC0 | — |
| 2 | `tv.arte.plus7` | ARTE | oui | Commons, domaine public | — |
| 3 | `com.canal.android.canal` | CANAL+ | oui | Commons, domaine public | — |
| 4 | `com.disney.disneyplus` | Disney+ | oui | Commons, domaine public | — |
| 5 | `fr.francetv.pluzz` | france.tv | oui | Commons, domaine public | — |
| 6 | `com.google.android.play.games` | Google Play Games | oui | Commons, domaine public | — |
| 7 | `com.android.vending` | Google Play Store | oui | Simple Icons, CC0 | — |
| 8 | `com.google.android.videos` | Google TV | oui | Simple Icons, CC0 | — |
| 9 | `com.wbd.stream` | HBO Max | oui | Simple Icons, CC0 | — |
| 10 | `com.netflix.ninja` | Netflix | oui | Simple Icons, CC0 | — |
| 11 | `fr.francetv.zouzous` | Okoo | oui | Commons, domaine public | — |
| 12 | `com.cbs.ca` | Paramount+ | oui | Simple Icons, CC0 | — |
| 13 | `com.amazon.amazonvideo.livingroom` | Prime Video | oui | Commons, domaine public | — |
| 14 | `com.spotify.tv.android` | Spotify | oui | Simple Icons, CC0 | — |
| 15 | `fr.tf1.mytf1.free` | TF1+ | **oui (ajout)** | Commons, CC0 | édition Freebox de TF1+ (« TF1+ : Streaming pour Freebox ») |
| 16 | `tv.twitch.android.app` | Twitch | **oui (ajout)** | Simple Icons, CC0 | — |
| 17–20 | 4 tuiles de la dernière ligne, coupées sur les captures | non lisible | un logo s'affiche, donc leur identifiant est déjà au catalogue | — | identifiants à relever sur une capture complète |
| 21 | `fr.tfou.max` | TFOU MAX (**ajout**) | non | — | aucun logo sous licence redistribuable |
| 22 | `fr.m6.m6replay.free` | identifiant | non | — | édition Freebox de M6+, absente de Google Play ; aucun logo M6+ sous licence |
| 23 | `net.oqee.androidtv` | identifiant | non | — | absente de Google Play ; seul logo Commons en CC BY-SA par un tiers : refusé |
| 24 | `net.oqee.launcher` | identifiant | non | — | **lanceur** OQEE, pas l'application |
| 25 | `com.nextinteractive.rmcbfmplay.tv.free` | identifiant | non | — | édition Free, absente de Google Play |
| 26 | `fr.free.aktu` | identifiant | non | — | application Free, absente de Google Play |
| 27 | `fr.freebox.disneypluslauncher` | identifiant | non | — | **lanceur Freebox** de Disney+, pas Disney+ |
| 28 | `fr.freebox.fbx8lc.fbxdiags` | identifiant | non | — | outil de diagnostic Freebox |

Bilan : 20 applications avec logo, 8 sans (raisons ci-dessus). Anomalie relevée : `fr.m6.m6replay.by` (nom « M6+ » dans le backend) n'existe pas sur Google Play ; l'identifiant vérifié de M6+ est `fr.m6.m6replay`, ajouté.

- [x] Logos et noms ajoutés : Twitch, TF1+ (édition Freebox), noms TFOU MAX et M6+ ; tests de correspondance et de repli.
- [ ] **Propriétaire** : une capture de la dernière ligne complète pour confirmer les identifiants 17–20.
- [ ] Logos OQEE, M6+, TFOU MAX : seulement si une source sous licence redistribuable apparaît (ou un accord écrit des éditeurs).

### C. Recette matérielle des deux télécommandes — après correction de l'UI

Plan détaillé, commandes et autorisations : [`docs/HARDWARE_ACCEPTANCE_PLAN.md`](docs/HARDWARE_ACCEPTANCE_PLAN.md) ; étapes manuelles, ordre des séances et fiche de preuve : [`docs/MANUAL_ACCEPTANCE_PROTOCOL.md`](docs/MANUAL_ACCEPTANCE_PROTOCOL.md). Constat réel déjà acquis : la liste des 28 applications s'affiche sur Ubuntu (débogage activé sur la Freebox, captures du 2026-10-17).

**Préparation de la recette — blocages corrigés (branche `fix/hardware-acceptance-readiness`, validation automatisée seulement) :**

- [x] Identité du paquet Ubuntu : le runner exige un manifeste de provenance (commit, run CI, version, architecture, SHA-256 du `.deb`, de `usr/bin/control-tv` et du contenu complet via `md5sums`, pont Python compris — le binaire seul est reproductible), recalcule les empreintes et refuse une version 0.1.0 d'un autre build ; la CI publie `provenance.json`.
- [x] Signature Android stable : prise en charge d'une clé de test `control-tv-test` (Gradle, CI par secrets, vérification contre un certificat épinglé, `apk-provenance.json`) — [`docs/ANDROID_TEST_SIGNING.md`](docs/ANDROID_TEST_SIGNING.md).
- [x] Paquet Ubuntu : dépendances d'exécution déclarées pour FFmpeg et les commandes X11
  (`ffmpeg`, `x11-xserver-utils`) ; la CI vérifie leur installation avec le `.deb`, puis la purge
  complète de `control-tv`.
- [ ] **Propriétaire** : créer la clé, la sauvegarder, définir les deux secrets et publier l'empreinte du certificat ; puis une dernière désinstallation sur chaque téléphone (les APK installés ont des signataires éphémères).
- [x] Runner : les scénarios TV, téléphone, double télécommande et latence indiquent qu'aucun adaptateur automatique n'existe et renvoient au protocole manuel.
- [x] Artefacts CI de `main` `de31c94` (run `39034444658`) téléchargés et identifiés dans `dist/acceptance-de31c94/` (ignoré par git) ; le binaire installé sur Ubuntu est ce build. APK : signataire éphémère, expire sur GitHub le 2026-10-24.
- [x] Suites de revue Codex : `check-apk` (APK exactement celui de sa provenance et du run attendu) et `check-deb` (poste vierge = non installé, fichiers installés vérifiés contre dpkg, aussi dans le runner).
- [x] Revue Codex de `db3ef0c` : empreinte CI de l'APK obligatoire, certificat épinglé illisible = BLOCKED, `check-deb` = « non installé » seulement sans enregistrement dpkg ni fichier du paquet (tout état dpkg incomplet = BLOCKED).
- [x] Instrumentation C6/C7 : trace des commandes, maintien de confirmation, observateur indépendant et validateurs hors ligne (`docs/COMMAND_TRACE.md`) ; validée en automatique seulement, C6/C7 restent NOT RUN sur matériel jusqu'à autorisation.


- [ ] Ubuntu : autorisation explicite avant toute installation du `.deb` ; lancement réel, bridge IPC, icône système, logs, fermeture et CSP.
- [ ] Android : autorisation explicite avant installation de l'APK ; lancement, affichage, widget, CSP et journaux ; préserver les données existantes.
- [ ] Google TV : obtenir l'accord explicite du propriétaire **avant toute exécution**, y compris R1–R4 bien qu'ils soient en lecture seule ; obtenir également cet accord avant chaque étape C1–C7 et avant toute autre commande TV. Toute activation ADB exige elle aussi un accord explicite préalable. Exécuter ensuite, selon `docs/DUAL_CONTROLLER_VALIDATION.md`, R1–R4, C1–C7, TV-POWER, TV-KEYBOARD, TV-APPS, TV-ERRORS et la concurrence des deux télécommandes.
- [ ] Latence : K1–K3 et `latency.remote-keys`, mesures vidéo et seuils à adopter par le propriétaire.
  - [x] Classement hors ligne de K1/K2 à partir des numéros d'images relevés sur la vidéo : `scripts/acceptance_latency.py key-step` (exactement dix pressions, règles PASS/FAIL/NOT RUN du protocole, médiane et pire délai, critère vérifié seulement s'il est fourni) ; validé en automatique uniquement.
- [ ] Distinguer systématiquement PASS simulé, PASS CI et PASS matériel ; rapport de preuves par scénario.
- [ ] Mettre à jour `DEVELOPMENT_PLAN.md`, `DEVELOPMENT_CHECKLIST.md`, README et rapports à l'issue des essais.

### D. Nouvelle fonctionnalité après recette : affichage Ubuntu X11 vers Google TV

**Périmètre conservé :** plusieurs appareils et plusieurs protocoles peuvent coexister derrière
les ports communs, mais toute découverte, commande, publication et réception restent exclusivement
sur le réseau domestique. Aucun serveur permanent ni exposition Internet n'est introduit.

- [x] **Étude de faisabilité (2026-10-17, documentation seulement) :** [`docs/REMOTE_DISPLAY_STUDY.md`](docs/REMOTE_DISPLAY_STUDY.md) — récepteurs (HLS vers le récepteur Cast par défaut, protocole de recopie Cast Streaming, récepteur web dédié, Sunshine/Moonlight, VNC), capture et encodage, écrans virtuels X11 (evdi, têtes virtuelles du pilote, `xrandr --setmonitor`), architecture et interfaces, sécurité, critères RD1–RD8, risques et estimations.
- [ ] **Phase D0 — implémentation et validation automatisée terminées, validation de plateforme
  ouverte :** inventaire et encodage synthétique documentés ; essais bornés du service avec le
  vrai FFmpeg en logiciel puis sur le GPU local encore à exécuter.
  - [x] Inventaire et encodage synthétique VA-API CQP mesurés à la main (PR #79) : Intel UHD, `iHD`, CQP seul (le mode débit échoue), 1920×1080@30, QP 24, 4,1× le temps réel.
  - [x] **Socle d'encodage local (issue #80)** : `control_tv.screencast` — valeurs validées, sources en spécifications typées (seule `SyntheticSource`, acceptée par type exact et revalidée avant toute sonde ou tout processus ; seul l'adaptateur FFmpeg la traduit en arguments), commandes FFmpeg bornées (VA-API CQP, repli `libx264` explicite ; 120 s, 256 Mio), sonde et choix d'encodeur, groupes de processus arrêtés en entier (SIGTERM puis SIGKILL ; le leader n'est récupéré qu'une fois le groupe vide, donc l'identifiant de groupe n'est jamais recyclé ; un groupe qui survit à SIGKILL fait échouer la session et est conservé pour `reclaim()`), machine d'états `ScreenCastService` (même contrôle du résultat pour `wait()` et `stop()`, nettoyage garanti, un appel à la fois) ; 153 tests automatisés (doublures, et vrais groupes de processus leader/descendant), sans GPU ni réseau en CI ; [`docs/SCREENCAST_D0.md`](docs/SCREENCAST_D0.md). Non exécuté sur le matériel dans cette PR.
  - [x] Parcours automatisé intégré : sonde, sélection avec repli explicite, service, vraie
    gestion de groupe de processus, attente/arrêt et nettoyage ; exécutable FFmpeg simulé, donc
    aucune revendication sur l'encodeur de la machine.
  - [ ] Essais réels du socle sur la machine (encodage synthétique via le service, `libx264` puis
    VA-API CQP) — **avec accord**. Les essais de réception HLS/Cast appartiennent à D1.
- [x] **D1, voie A — publication HLS et récepteur Cast (logiciel) :** `HlsPublisher` (serveur HTTP par session sur une adresse du réseau domestique, port et jeton aléatoires, playlist et segments seulement, durée bornée, arrêt idempotent) et `CastHlsReceiver` (un `load_media` via la couche partagée, session seulement si confirmée, n'arrête jamais un autre contenu) ; validés en automatique uniquement (boucle locale et faux transport), aucune compatibilité Freebox annoncée.
- [x] **D1, commande :** `python3 -m control_tv.screen_streaming.d1 --no-tv` produit un vrai flux HLS H.264 (image synthétique, libx264) servi en boucle locale ; essai réel fait sur cette machine. `--device ID` (découverte, adresse LAN, chargement sur le récepteur Cast) reste à lancer **avec accord**.
- [x] **D1, capture X11 réelle :** `d1 --screen SORTIE` capture une sortie X11 (lecture `xrandr --listmonitors`, `x11grab`, mise à l'échelle) ; essai réel sur un affichage Xvfb privé (jamais le bureau du propriétaire).
- [ ] **Phase D1 — duplication :** prototype isolé de capture X11 et encodage, diffusion sur Google TV avec récepteur compatible ; mesurer latence, fluidité, résolution et arrêt propre.
  - [x] Préparation indépendante sans matériel : contrats typés capture/encodage/publication/réception,
    orchestration synthétique et nettoyage testés sans capture, réseau ni TV ; comparaison HLS,
    Cast Streaming et Sunshine/Moonlight documentée sans revendiquer de compatibilité Freebox.
    Suites de revue : `close()` concurrent jamais perdu, nettoyage poursuivi malgré une interruption.
    Adaptateur d'encodage D0 → `ScreenEncoder` fusionné dans #86 : HLS synthétique en direct,
    cycle de vie et nettoyage testés sans capture, réseau ni TV ; publisher HLS, récepteur et
    capture X11 fusionnés dans #92 ; surveillance complémentaire du publisher dans #95.
  - [ ] Faire relire et intégrer #95, puis exécuter sur autorisation distincte les essais de
    réception HLS/Cast. La duplication réelle reste non validée.
- [x] **D2, écran virtuel (logiciel) :** `d1 --extend --yes-change-display` ajoute un moniteur logique `control-tv-virtual` à droite (`xrandr`, sans fichier de configuration), le diffuse et le retire à l'arrêt ; validé avec un faux `xrandr` ; non vu sur le bureau réel.
- [ ] **Phase D2 — extension :** étudier et tester un véritable affichage virtuel X11 indépendant, son intégration avec le gestionnaire de fenêtres et la diffusion du seul écran virtuel ; vérifier les capacités du GPU/driver Dell.
- [x] **D3, carte « Screen to TV » (logiciel) :** Dupliquer / Étendre / Arrêter, choix de l'écran, résolution, FPS, étape de consentement, états et erreurs ; affichée seulement sous X11 ; tests unitaires ; contrôle visuel le 2026-10-19 sur Xvfb privé (application réelle sans TV, puis états consentement/flux/échec avec un shell fictif) et défauts corrigés ; jamais utilisée avec la TV. Budget du bundle JavaScript relevé de 64 à 72 Kio (62,0 → 69,0 ko, modules locaux seulement).
- [x] **D3, gestionnaire d'affichage (logiciel, PR #97) :** écrans et modes lus à chaque ouverture par `xrandr --verbose`, rectangles numérotés proportionnels, sélection individuelle, placement libre avec magnétisme, Joindre/Cloner/Écran unique, résolution, fréquence, orientation, échelle et écran principal ; Appliquer ne conserve rien sans confirmation et Annuler/Rétablir repassent par le bridge Python autoritatif. Tests automatisés et CI uniquement ; aucune validation matérielle de cette interface.
- [x] **D4, protections logicielles :** consentement exigé à chaque démarrage, un flux à la fois, une heure au plus, arrêt sur Stop, échec ou fermeture de l'application ; procédure de recette documentée (`docs/SCREENCAST_TV_TEST_PROCEDURE.md`), non lancée.
- [x] **Phase D3 — intégration UI (logiciel) :** actions explicites « Dupliquer », « Étendre », « Arrêter » ; sélection TV/écran, résolution et FPS ; gestionnaire d'affichage complet ; aucun lancement automatique de capture.
- [ ] **Phase D3 — validation matérielle :** utiliser le gestionnaire et les actions de diffusion dans l'application installée, sur la topologie réelle, puis consigner les observations sans assimiler la CI à cette preuve.
- [ ] **Phase D4 — sécurité et validation :** consentement explicite à la capture, arrêt et nettoyage garantis, LAN/authentification, aucune exposition non voulue, tests Ubuntu/TV réels, documentation et rapports.
- [x] Le propriétaire a explicitement repriorisé D0 puis la préparation D1 avant la clôture de la recette ; les opérations matérielles restent soumises à des accords séparés.
- **Dépendances et priorités actuelles :** D0, les contrats D1 et leur adaptateur sont dans
  `main` (#81, #83, #86, #92). Prochaine étape : revue et intégration de #95, puis preuve Freebox
  autorisée. La recette télécommande et le packaging (#84/#85) suivent leur
  chaîne matérielle séparée, sans être recomptés dans D4.
- **Suivi :** les tâches et preuves figurent ici ; toute estimation de temps est réservée à la conversation.
- **Autorisations nécessaires :** toute diffusion réelle vers la TV ; installation d'une application sur la TV (Moonlight, si cette voie est retenue) ; module noyau evdi ou modification de la configuration Xorg ; démon hôte (Sunshine) pendant la diffusion.

**Organisation :** Codex implémente sur branche dédiée ; Claude révise indépendamment ; PR, CI et preuve de rendu avant fusion ; aucune modification directe de `main`.


État au 2026-10-15, `main` `d95fc7b`. Les corrections d'audit et la CI de packaging sont terminées ; les validations matérielles restent distinctes et ouvertes. La référence complète reste [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) (section « Plan complet et suivi du temps ») et [DEVELOPMENT_CHECKLIST.md](DEVELOPMENT_CHECKLIST.md).

**Objectif :** deux télécommandes autonomes, Ubuntu et Android, qui contrôlent la même Google TV en parallèle, sans serveur permanent. MCP, ChatGPT Voice et Windows sont abandonnés depuis le 2026-10-12.

**Règles :** aucune fusion et aucune commande envoyée à la TV sans l'accord explicite du propriétaire. Une CI verte n'est pas une validation sur matériel.

## 1. Intégration de l'audit — terminée le 2026-10-15

- [x] Neuf PR fusionnées dans cet ordre : #57 (`124ea7c`, `source-map-js`), #63 (`ad39a92`, `pip`/`wheel`), #58 (`3b2c51c`, audit npm), #64 (`dfe8ebb`, audits Python/Rust), #59 (`23852fd`, ciblage `mediaSessionId`), #60 (`5cd8570`, validation numérique `MediaStatus`), #62 (`42a73ee`, corrélation Rust/Python), #56 (`8bef0a5`, Unicode), #61 (`d95fc7b`, CSP Tauri/IPC).
- [x] CI `main` `d95fc7b`, run `38723748264` : quatre jobs verts ; Python 1 481 tests et 98,85 % de couverture ; UI 335 ; Rust 77 ; Kotlin 147 ; audits npm, Python et Rust réussis ; APK arm64 et `.deb` construits ; `.deb` installé, testé et désinstallé dans la CI.
- [x] Dérogation Rust limitée à `RUSTSEC-2024-0429` / `glib` 0.18.5, expiration **2027-01-15** ; l'expiration fait échouer la CI. Aucun autre avis n'est couvert.
- [x] Artefact `.deb` exact récupéré du run : `control-tv-linux-release` (id `11967735137`), sommes SHA-256 valides ; paquet `control-tv` 0.1.0 `amd64`, pont Python CPython 3.12.15 présent, aucun fichier de développement inutile détecté. Non installé localement. Doublon non bloquant de `libayatana-appindicator3-1` dans `Depends` à nettoyer ultérieurement.
- [x] Dérogation locale : ne pas modifier uv global 0.11.26, qui ne connaît pas Python 3.12.15 ; la construction, l'installation, le test et la purge CI du `.deb` constituent la validation de packaging retenue. Cela ne vaut pas validation matérielle.

## 1 bis. Fusions antérieures

- [x] **#41, #42, #43** : canal télécommande dans le `.deb` et l'APK, clavier distant, nettoyage du plan. Fusionnées.
- [x] **#38** : tests de convergence Ubuntu/Android. Corrections P3 déjà faites ; branche remise à jour avec `main`, puis fusionnée (CI `main` 4/4 verte).
- [x] Code MCP de `main` (PR #31) : **conservé tel quel**, sans développement (décision du propriétaire).
- [x] **#33** (widget Android) et **#49 à #53** (corrections de l'audit de `main`) : fusionnées le 2026-10-13 ; la documentation annulée temporairement par #53 a depuis été rétablie.

## 1 ter. Qualité du code — terminé le 2026-10-13

- [x] Compétence `code-solid` appliquée ; plus de `Any` ni de `unknown` dans le code (les tests peuvent en garder) ; mypy refuse désormais tout `Any` explicite hors tests.
- [x] 2026-10-23 (branche `chore/graphify-agent-integration`) : Graphify intégré proprement à la configuration des agents (skill unique `.agents/skills/graphify` lié pour Claude, règles dans `AGENTS.md`, `/graphify-out/` et `/.worktrees/` ignorés et vérifiés par `agent_config.py check`).
- [ ] Après accord du propriétaire, dans le checkout principal : retirer les copies non suivies `local-device-mcp-bridge` et `project-bootstrap-context` de `.agents/skills/` et `.claude/skills/` (identiques à `507e052`, archivées), et remplacer la copie locale `.claude/skills/graphify/` par le lien versionné lors de la mise à jour de `main`.

## 1 quater. Corrections de l'audit de `main` (2026-10-13)

- [x] **P3-3** : message d'erreur ADB — code `debugging_off` distinct, aide « débogage USB » seulement dans ce cas ; tests Python et UI. Fusionnée (#49).
- [x] **P2-1, P3-1, P3-2** : README, ARCHITECTURE et TODO actualisés (MCP / Voice / Windows abandonnés, icône système fusionnée) — rétablis après la fusion de #33.
- [x] **P3-4** : le bridge dépend du port `TvControl` complété plutôt que du `ControlService` concret. Fusionnée (#51).
- [ ] **P3-6** : mesurer la latence des touches (flèches) pendant la recette matérielle. Étapes K1–K3 fusionnées (#52) ; mesure à faire.

## 2. Phase 1 — les deux applications sur la même TV

- [ ] **R1 à R4** (lecture seule) : Ubuntu et le téléphone ouverts en même temps, sur la même TV. Résultat PASS/FAIL/NOT RUN pour chaque test. Avancement : partiel, non classé.
- [ ] Vérifier sur Android que la découverte signale bien le canal télécommande de la TV.

## 3. Phase 2 — essais sur la TV (avec autorisation, propriétaire devant la TV)

| Fonction | Ubuntu | Android | Ce qui manque |
|---|---|---|---|
| Volume +/−, Mute (son réel) | ✅ validé le 2026-10-12 | codé, 0 % confirmé | installer l'APK de #41 |
| Power (veille / réveil) | codé ; depuis la PR du pavé, « confirmé » seulement si la TV rapporte son nouvel état d'alimentation (attente bornée à 4 s), testé en simulation ; 0 % sur TV | idem (même couche Python) | essai : réveil depuis la veille, mise en veille, délai réel du rapport d'état ; une TV en veille profonde (réseau coupé) reste injoignable par ce canal, le Wake-on-LAN n'est ni codé ni vérifié sur la Freebox |
| Pavé directionnel (rendu) | corrigé le 2026-10-19 : symétrique, sans chevauchement, états survol/appui/focus ; vérifié sous WebKitGTK sur Xvfb privé avec données fictives | même interface | contrôle visuel dans l'application installée |
| Flèches, OK, Retour, Accueil | codé, 0 % confirmé | codé, 0 % confirmé | essai, et mesure de la latence par touche (étapes K1–K3 de `docs/DUAL_CONTROLLER_VALIDATION.md`, vidéo) |
| Clavier distant (texte, Backspace, Entrée) | codé (#42), 0 % confirmé | codé (#42), 0 % confirmé | essai : TV allumée, recherche YouTube ouverte |
| Application active affichée | codé (#42), 0 % confirmé | codé (#42), 0 % confirmé | essai |
| Applications installées : liste et lancement | codé, 0 % confirmé | codé, 0 % confirmé | essai : activer le débogage USB sur la TV, autoriser control-TV, lister, ouvrir |
| Lecture, pause, stop, seek | partiellement observé | 0 % | essais C1–C7 autorisés |

- [ ] **Critère de latence des touches — ta décision :** proposé (non adopté) : délai envoi → effet médian ≤ 0,5 s et maximal ≤ 1,0 s, délai envoi → télécommande prête médian ≤ 1,0 s, sur Ubuntu et sur Android. Tant qu'il n'est pas adopté, K1 et K2 mesurent sans juger la latence.
- [ ] **Téléphone :** le brancher en USB, accepter le débogage, et autoriser l'installation de l'APK de la CI. Si les signatures diffèrent, le widget actuel sera perdu.
- [ ] **Applications installées (2E) — à essayer :** la liste passe par le débogage de la TV (ADB), **désactivé tant que tu ne l'actives pas** : Paramètres › Système › À propos › « Build de l'OS Android TV » 7 fois, puis Options pour les développeurs › Débogage USB. La première fois, la TV demande d'autoriser control-TV. Sur Ubuntu, le « Débogage sans fil » avec code d'appairage est désormais pris en charge par #99 lorsqu'un Android SDK Platform Tools externe >= 30 est disponible ; le paquet `.deb` ne garantit encore que l'ADB TCP classique, et l'application Android ne prend pas encore en charge ce transport TLS. Aucun de ces chemins n'a été validé sur la TV du propriétaire. L'ouverture d'une application passe aussi par ADB : le débogage doit rester actif pour ouvrir les applications depuis control-TV.
- [ ] Commandes concurrentes C1–C7 depuis Ubuntu et Android, avec autorisation.

## 4. Widget Android — PR #33 (fusionnée le 2026-10-13)

- [x] Conflits résolus et branche remise à jour avec `main` (2026-10-13, CI 4/4 verte) ; description de la PR actualisée ; revue Codex demandée.
- [x] Documentation du Mute corrigée : mute Cast confirmé par le récepteur, volume fixe, aucun effet audible (le vrai mute passe par le canal télécommande).
- [ ] Tester sur le téléphone, avec ton accord pour chaque commande : Lecture/Pause, Stop, Volume.
- [x] Mute et Volume −/+ du widget passés par le canal télécommande (le son réellement entendu, 2026-10-25) ; ils ne dépendent plus d'un volume Cast que la TV ne rapporte pas. Validation automatisée seulement.
- [x] **Widget enrichi (demande du propriétaire, 2026-10-25) :** sous la ligne lecture/volume, la télécommande de la TV : flèches autour d'OK, Retour et Accueil, Power et Google Assistant ; taille par défaut 4×4, réductible jusqu'à l'ancienne hauteur (les rangées de la télécommande sont alors masquées). Chaque touche est envoyée une seule fois, sans lecture Cast préalable ; TV non appairée : « Pair this TV in control-TV first ». Tests JVM ; pas encore vu sur le téléphone.
- [ ] Essai réel du widget enrichi sur le téléphone (agrandir le widget existant, chaque touche une fois).
- [x] **Issue #112 (lenteur et apps sur Chambre parentale, 2026-10-25) :** la connexion de télécommande vers une TV est réutilisée tant que la bibliothèque la tient ouverte (fermée après 30 s sans usage, après tout échec, à l'appairage) au lieu d'une session TLS par touche ; les touches de volume n'attendent plus un volume que la TV ne rapporte pas ; le téléphone journalise la durée de chaque requête (`adb logcat -s control-tv`, méthode, ms, résultat, sans identifiant). Bouton « Open to the phone » sur l'ordinateur : ouvre le port 5555 de la TV appairée en débogage sans fil (`adb tcpip 5555`, jusqu'au redémarrage de la TV) pour que le téléphone liste ses apps ; message du téléphone adapté. Tests automatisés seulement.
- [x] **Issue #112, Ubuntu :** l'appli lancée depuis GNOME n'avait que l'`adb` d'Ubuntu (28, sans débogage sans fil) car le SDK n'est dans le `PATH` que via `~/.bashrc` ; control-TV cherche maintenant d'abord l'`adb` du SDK installé (`CONTROL_TV_ADB`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `~/Android/Sdk`). Tests automatisés ; pas encore vu sur le matériel.
- [x] Téléphone : une seule icône control-TV (2026-10-25) ; l'écran de recette des builds de debug n'a plus d'icône, il s'ouvre depuis l'ordinateur (`docs/COMMAND_TRACE.md`).
- [x] **Issue #112, sans réglage de la TV (demande du propriétaire, 2026-10-25) :** ouvrir une app passe par la télécommande de la TV (appairée une fois, lien `market://launch` de la TV), sans débogage ; le débogage ne sert plus qu'à la liste. Si la TV ne répond pas en débogage, la fenêtre montre sa dernière liste (gardée sur l'appareil, fichier privé par TV), en le disant ; jamais d'apps non lues sur cette TV (la liste générique d'« apps connues » a été retirée le 2026-10-26 : le propriétaire y voyait des apps qui ne sont pas sur Chambre parentale). Ouvrir une app avec une télécommande non appairée demande maintenant d'appairer la télécommande. La vérification du récepteur pour la diffusion reste stricte (jamais devinée). Tests automatisés ; pas encore vu sur le matériel.
- [x] **Issue #112, vraie liste sans réglage :** ControlTV Récepteur lit lui-même les apps installées sur la TV (avec leurs vrais noms) : la télécommande appairée ouvre son lien `controltv-receiver://apps`, un écran invisible envoie la liste une fois à l'appareil qui l'a demandée (écoute locale à usage unique, jeton aléatoire, 8 s), puis se ferme. Ordre : débogage, récepteur, dernière liste, sinon aucune. Nécessite d'installer une fois cette version du récepteur sur chaque TV. Tests automatisés ; pas encore vu sur le matériel.
- [ ] **Sprint 1 T-02 — ouvrir les apps de Chambre parentale (Ubuntu et Android) :** cause confirmée par le journal de la TV (`market://launch` refusé, `ActivityNotFoundException`, `INVALID_ARGUMENT`) alors que ControlTV affichait « envoyé ». Correctif implémenté (tests automatisés seulement) : lancement par ControlTV Récepteur (`controltv-receiver://launch`), réponse réelle de la TV (démarrée / non installée / refusée), aucune relance, écrans invisibles fermés avant le réseau. Revue du 2026-10-28 (auto-revue de Claude, auteur du correctif : une revue indépendante reste nécessaire) : tests ajoutés pour deux chemins d'erreur non couverts. Reste : mettre à jour le récepteur sur la TV, puis recette interactive avec le propriétaire (Ubuntu, téléphone, vidéo en cours).
- [x] **T-01, identités d'appairage distinctes (corrigé le 2026-10-30) :** la recette du 2026-10-28 a confirmé que #114 distinguait les noms mais conservait deux certificats historiques `CN=control-TV`, si bien qu'un appairage évinçait l'autre. Chaque installation Ubuntu ou Android conserve maintenant une identité et un certificat au sujet identique. La paire historique est sauvegardée puis remplacée une seule fois ; un dernier appairage explicite par TV est nécessaire, jamais une régénération au lancement. Tests simulés : coexistence, alternance, redémarrage, persistance et migration ; la recette matérielle reste à refaire avec le propriétaire.
- [ ] Issue #112 sur le matériel : mesurer la latence téléphone → Chambre parentale avec les journaux ; appairer le débogage sans fil depuis l'ordinateur, Open to the phone, puis Show TV apps sur le téléphone. La cause de la lenteur n'est pas confirmée sur le matériel.
- [x] Fusion de la #33 : faite le 2026-10-13, avant les essais sur téléphone (`1b86f47`). Mute sur volume Cast fixe affiché « Muted (Cast) · TV sound may not change ».

## 5. Phase 3 — livraison

- [ ] **Refonte de l'interface (demande du 2026-10-13)** : télécommande d'abord (pavé directionnel rond, volume TV en bascule, Retour/Accueil), applications de la TV en grille de tuiles, clavier repliable, son Cast en dernier ; thème clair uniquement, cibles tactiles ≥ 48 px, contrastes AA. Branche `feat/ui-redesign`, vérifiée sur captures (TV fictive) ; à voir sur le téléphone et sur Ubuntu.
- [ ] Installer le `.deb` final sur le PC et l'APK final sur le téléphone, puis refaire la recette complète. La CI a validé construction/installation/test/purge du `.deb`, mais aucune installation locale de l'artefact `d95fc7b` ni recette sur TV n'a été faite.
- [ ] Documentation d'utilisation : appairage, clavier, limites (par exemple, cette TV ne rapporte pas son volume).
- [ ] Icône dans la barre système Ubuntu (élément K) : **fusionnée** (#46, 2026-10-13), pas encore vue sur un vrai bureau — Ouvrir, Lecture/Pause, Stop, Mute TV, Volume TV −/+, Quitter ; fermer la fenêtre quitte l'application (décision à confirmer). À voir sur ton bureau Ubuntu.

## Suivi

| Lot | Avancement | Prochaine preuve |
|---|---|---|
| Phase 1 : autonomie et convergence |  tests simulés 100 %, fusionnés (#38) ; matériel partiel, non classé | Recette matérielle R1–R4 autorisée |
| Phase 2 : commandes, applications, clavier |  son validé sur Ubuntu ; le reste codé, 0 % confirmé sur TV ; applications 0 % | Commandes sur TV et décision ADB du propriétaire |
| Phase 3 : livraison |  CI `d95fc7b` verte, APK et `.deb` construits, `.deb` installé/testé/purgé en CI ; recette matérielle finale à faire | Installation et recette finale autorisées |
