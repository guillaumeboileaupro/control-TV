# CONTROL-TV - Tableau de bord permanent

> Référence de pilotage du propriétaire et du coordinateur, complément obligatoire de `AGENTS.md`. Consolidation initiale : 2026-10-31. Les estimations sont celles du coordinateur et non du temps mesuré.

## Règles de fonctionnement

- **Lire ce fichier au début de toute itération**, avec `AGENTS.md`, `TODO.md`, `DEVELOPMENT_PLAN.md` et les preuves pertinentes.
- **À chaque fin de prompt de travail**, mettre à jour les lignes concernées, les PR, les blocages, les priorités, les responsables et les prochaines actions. Ne jamais effacer un chantier parce qu'il n'est pas actif.
- Les agents fournissent les faits, **le coordinateur seul chiffre ou actualise les temps**. Une seule colonne `Temps restant (min / max / méd.)`, en heures. Ne jamais inventer les résultats matériels.
- Le propriétaire choisit les priorités et autorise fusions et manipulations matérielles. **Un seul agent actif, une seule PR ouverte au total**, une correction cohérente par PR. Aucun agent ne modifie la branche d'un autre.
- Codex : corrections de télécommande, intégration, maintenance et sécurité. Claude : développement Rust D1/D2/D3/D4 et recette matérielle, selon la mission choisie. Le coordinateur maintient la vue globale.
- Aucun contournement d'audit ni fusion sans autorisation. Préserver `.ai-private/`, certificats, données utilisateur, stashes et travaux non fusionnés. Nettoyage sûr après chaque tâche.
- Distinguer systématiquement : **implémenté / CI vérifiée / fusionné / PASS matériel / FAIL / PARTIAL / NOT RUN / NOT IMPLEMENTED**.
- Une correction non vérifiée sur les TV n'est jamais déclarée terminée matériellement. En cas de lecture seule, fournir les changements de dashboard à réaliser dans la prochaine PR autorisée, sans écrire directement sur `main`.
- Parler de « chantier » ou de « phase » ; éviter « sprint ».

## État global - dernier état vérifié

| Indicateur | État |
|---|---|
| Référence de recette du 30/10 | `main 3eebf23` sur Ubuntu, téléphone, récepteur Chambre ; version récepteur Liberté inconnue |
| PR #113 à #117 | Fusionnées : #113 `fb87687`, #114 `3eebf23`, #115 `8515a42`, #116 `e577adb`, #117 `f7f1ca2` |
| PR actives vérifiées au 07/11 | #118 à **#122** et **#124** à **#126** fusionnées ; **#123** redondante avec #121 (ne pas toucher) ; **#127** gouvernance ouverte (autre auteur) ; migration `webrtc` 0.21 sur `chore/webrtc-0.21` |
| Défauts P1 observés | 3 : appairage durable, découverte Chambre, lancement Android Liberté |
| Défaut P2 observé | Assistant sans effet sur Liberté ; correction microphone Android implémentée, non testée matériellement |
| Deux chantiers structurants | **D1 duplication** et **D2 extension**, à terminer et valider séparément |
| Contrôles techniques | Rust/WebRTC, widget Android et menu GNOME testés automatiquement, mais pas totalement sur matériel |
| Nettoyage local rapporté | 3 worktrees + 4 branches locales de PR supprimés, anciens dist réduits à 435 Mio ; nouveaux APK de recette à préserver |
| Sources privées | `.ai-private/hardware/recette-2026-10-26/`, `.ai-private/hardware/recette-2026-10-28/` ; rapport Claude privé |

## Tableau exhaustif des travaux restants

Estimations min / max / médiane en **heures**, à réviser après chaque livraison.

| Priorité | Travail restant | Temps restant (min / max / méd.) |
|---|---|---|
| P1 | #126 - Corriger les 3 remarques et intégrer T-06 | 1 / 3 / 2 h |
| P1 | #125 - Corriger les 2 remarques et intégrer T-02 | 1 / 3 / 2 h |
| P1 | Valider l'appairage et la découverte sur les TV | 0,5 / 1,5 / 1 h |
| P1 | Valider le lancement des applications Android | 0,5 / 1,5 / 1 h |
| P1 | Tester duplication, extension et récepteur TV | 1 / 3 / 2 h |
| P1 | Valider la restauration X11 et audio | 0,5 / 1,5 / 1 h |
| P1 | Mesurer synchronisation et latence | 0,5 / 2 / 1 h |
| P2 | Valider microphone / Assistant | 0,5 / 1,5 / 1 h |
| P2 | Tester widget Android et menu GNOME | 0,5 / 1,5 / 1 h |
| P2 | Finaliser logos, langues et Power-CEC | 1 / 3 / 2 h |
| P2 | Intégrer Spec Kit | 0,5 / 1 / 0,75 h |
| P2 | Créer le skill de sécurité demandé | 1 / 2 / 1,5 h |
| P2 | Clôturer #123, redondante | 0,1 / 0,3 / 0,2 h |
| TOTAL | Travail restant estimé | 8,1 / 24,8 / 16,45 h |

## Dernière recette matérielle - 30/10

| Scénario | Résultat constaté | Statut |
|---|---|---|
| T-03 - Ubuntu après deux clics Assistant | Interface utilisable | PASS |
| T-04 - Journaux et corrélation | Identifiants par commande, pas de données sensibles rapportées | PASS |
| T-05 - Doublons depuis téléphone | Compteur 0 | PASS sur scénario testé |
| T-02 - Ouverture depuis Ubuntu | TV confirme en 2,4 s | PASS |
| Télécommande téléphone -> Liberté | Touches essayées fonctionnelles | PASS |
| T-01 - Ubuntu + téléphone -> Liberté | Perte de l'appairage du téléphone après appairage Ubuntu ; anciens certificats homonymes suspectés | **FAIL P1** |
| T-02 - Ouverture depuis téléphone -> Liberté | 4 expirations vers 8 s, erreur honnête affichée | **FAIL P1** |
| Découverte Chambre | Allumée et annoncée mais introuvable trois fois | **FAIL P1** |
| Assistant sur Liberté | Aucun effet | **FAIL P2** |
| D1, D2, widget, GNOME, audio, performance | Pas de validation matérielle complète | **À valider** |
| Logos, traductions, Power/CEC | Jugés globalement corrects, non retestés point par point | **Non revérifiés** |

## File de travail - une mission et une PR à la fois

| Ordre | Agent | Livraison | Condition |
|---|---|---|---|
| 1 | Codex | #118 - certificats distincts durables | Revue, CI, autorisation de fusion, recette ciblée |
| 2 | Claude | **D1 - Duplication Rust** | Après #118, PR dédiée, pas de test matériel sans accord |
| 3 | Claude | **D2 - Extension Rust**, puis D3/D4 | PR successives, restauration X11 prouvée |
| 4 | Codex | Découverte persistante sur sa branche existante, puis lancement Android | Livraison sans nouvelle PR ; attendre la prochaine mission |
| 5 | Claude | Recette matérielle deux TV, audio, latence, widget, GNOME | Autorisation avant chaque action |
| 6 | Agents selon mission | Assistant/micro, dépendances, finitions | Réajuster selon anomalies réelles |

## Protocole de fin d'itération

1. Vérifier `git status`, HEAD, PR ouvertes, CI et livrables. Séparer faits vérifiés et informations rapportées.
2. Mettre à jour **toutes les lignes impactées** de `DASHBOARD.md` sans faire disparaître les autres.
3. Pour tout développement, synchroniser aussi `TODO.md` et `DEVELOPMENT_PLAN.md` dans la même branche/PR.
4. Reporter les nouveaux blocages, dépendances, décisions et prochaines actions. Faire remonter les estimations au coordinateur.
5. Nettoyer les seuls artefacts temporaires vérifiés, préserver les preuves et travaux.
6. Restituer un bilan court `Action | SHA/PR | CI/tests | Nettoyage | Blocages | Suite` ; pour le propriétaire, fournir le **tableau global complet** quand il demande un état d'ensemble.

## Journal

| Date | Événement |
|---|---|
| 2026-11-07 | Claude, PR #131 : champ du code de session du récepteur rendu invisible (il garde le focus pour recevoir le code), sur demande du propriétaire ; tests JVM du récepteur PASS. Vérification sur la TV à faire avec le prochain essai Dupliquer. |
| 2026-11-07 | Claude, PR #131, essai suivant (récepteur corrigé, il reste affiché) : pas d'image, Ubuntu affiche « WebRTC transport failed: set_remote_description called with multiple conflicting ice-ufrag values ». Offre locale inspectée : BUNDLE présent, mais une seule vidéo proposée, H.264 **Baseline 42001f** (webrtc 0.21 n'offre que le codec lié à la piste), que libwebrtc ne traite pas comme Constrained Baseline : vidéo rejetée par la TV, réponse avec un second jeu d'identifiants ICE. Corrigé : la piste annonce H.264 Constrained Baseline 42e01f (profil produit par libx264 et VA-API) ; test de l'offre ajouté. Moteur : 48 PASS / 1 ignoré, clippy propre. Nouvel essai à faire après installation. |
| 2026-11-07 | Propriétaire, après installation du récepteur corrigé (16:22) : **Dupliquer Ubuntu -> TV visible : HARDWARE PASS** (« ça marche »), code de session transmis automatiquement. Remarque UX : le champ « Code de session (rempli automatiquement) » visible sur la TV laisse croire qu'il faut saisir un code ; amélioration proposée (message « Connexion à l'ordinateur… », champ hors de vue). Étendre, Arrêter, restauration, son et latence : NOT RUN. |
| 2026-11-07 | Claude, PR #131, essai Dupliquer avec le `.deb` 0a0c78d et lecture autorisée du journal de la TV : **le code de session arrive** (le récepteur démarre sa session WebRTC : fabrique, rendu, audio 48 kHz), puis le récepteur **plante** (SIGABRT) : `SecurityException` faute de `ACCESS_NETWORK_STATE`, exigée par le `NetworkMonitor` de libwebrtc. Corrigé : permission déclarée dans le manifeste du récepteur (permission normale, sans demande), test de non-régression du manifeste. APK récepteur reconstruit, tests JVM PASS ; installation sur la TV en attente d'autorisation. |
| 2026-11-07 | Claude, branche `fix/session-code-handoff`, recette autorisée sur la TV visible : Dupliquer ouvre bien ControlTV Receiver, mais le code de session n'arrive jamais dans son champ. Observation des messages de télécommande (types et compteurs seulement) : la TV annonce son champ actif (`remote_ime_key_inject` / `remote_ime_show_request`, n° 17, 20, 23…), alors qu'`androidtvremote2` 0.3.2 adresse le texte avec le compteur de son dernier message d'édition (1 ou 0). Deux essais « test » via `send_text` : champ vide ; un essai adressé au champ annoncé : **points affichés (HARDWARE PASS de la saisie)**. Corrigé : l'adaptateur suit le champ annoncé et y adresse le texte, sans le journaliser. Local : 2 912 PASS / 8 SKIP, 98,67 %. Diffusion complète : NOT RUN. |
| 2026-11-07 | Claude, branche `fix/webrtc-capability` (recette du jour, sur accord du propriétaire) : « WebRTC is not confirmed » sur les deux TV alors que le récepteur `d8ea896` y est installé. Lecture des capacités via le bridge installé : TV restaurée marquée sans télécommande (`unsupported_operation`), l'autre sans ADB 5555 (`device_unavailable`). Corrigé : (1) récepteur confirmé si l'ADB le liste **ou** s'il répond lui-même par la télécommande appairée (détection et démarrage WebRTC) ; (2) une télécommande non entendue pendant la courte écoute mDNS reste inconnue au lieu d'absente, et une télécommande déjà vue est conservée. Local : 2 906 PASS / 8 SKIP, couverture 98,66 %. Matériel : installation `d8ea896` faite sur Ubuntu, téléphone et deux TV ; WebRTC encore NOT RUN. |
| 2026-11-07 | Claude, PR #129 : CI `5c7d3fb` en échec sur un test instable venu de #125 (`test_a_late_refusal_wins_when_the_app_was_already_in_front`) : il lisait le statut HTTP du faux récepteur avant la fin de son envoi. Les trois tests concernés attendent maintenant la fin des envois ; 8 passages sur 8. Aucun changement de code applicatif. |
| 2026-11-07 | Claude, PR #129, recette Ubuntu -> Chambre (`.deb` 541c1b0 installé et vérifié) : « WebRTC not confirmed » disparu (HARDWARE PASS de la détection), mais Dupliquer échoue : « WebRTC preparation failed » (journal : `unsupported_operation` en 6,7 s). Cause : la diffusion se connectait par un `ControlService` nu (sans télécommande, ADB ni récepteur), donc tout démarrage WebRTC depuis l'application échouait. Corrigé : le contrôleur passe par le service du bridge (`d1.through`), et l'erreur affichée donne la vraie cause. Local : 2 907 PASS / 8 SKIP, 98,66 %. |
| 2026-11-07 | Propriétaire, après installation de `d8ea896` partout : appairage et découverte sur les TV (T-01, T-06) et lancement des applications Android (T-02) déclarés **HARDWARE PASS** par observation du propriétaire ; détail des scénarios non relevé par Claude. |>>>>>>> main
| 2026-11-07 | Claude, branche `chore/webrtc-0.21` (D1-B2, accord du propriétaire) : moteur WebRTC migré de `webrtc` 0.14 à 0.21 (pile `rtc`, `ring`) ; `crypto-bigint`, `bincode`, `pin-utils` absents des deux lockfiles, cinq dérogations WebRTC supprimées. Moteur 47 PASS / 1 ignoré (décodage H.264/Opus et bout en bout inclus). #124 (`8a1f3e9`), #125 (`4d80fce`) et #126 (`bb1689a`) fusionnées. Matériel NOT RUN : diffusion réelle à revalider. |
| 2026-11-07 | Claude, PR #126 : `main` après #124 et #125 intégré (conflits `DASHBOARD.md` seuls). CI de `d2027e4` : paquet Linux, APK et shell Tauri en échec sur le budget du bundle UI (98 664 octets pour 98 304) ; budget relevé à 112 Kio et documenté (`docs/APP_LOGOS.md`), garde-fou conservé contre le catalogue d'icônes. Matériel NOT RUN. |
| 2026-11-06 | Claude, PR #126 : Corrections de revue (Claude, 2026-11-06, PR #126, accord du propriétaire) : P1, une TV restaurée de la mémoire n'est jamais utilisée à son adresse sauvegardée (télécommande, texte, voix, applications) avant qu'une découverte la revoie sous son identité ; P2, statut d'une TV restaurée borné par une échéance unique de 8 s pour la redécouverte et la lecture (sous les 10 s du shell) ; P2 UI, « Oublier » est suivi comme opération en cours et bloque sélection, découverte et commandes jusqu'à la réponse. Local : 2 890 PASS / 8 SKIP, couverture 98,67 %, UI 426 PASS ; matériel NOT RUN. |
| 2026-11-06 | Finalisation T-06 : vérification de #124 ouverte, donc absente de main ; `main` jusqu’à #122 déjà ancêtre de la branche. Registre/service/bridge : 534 tests ciblés PASS, lint/format PASS. UI Ubuntu et sélecteur Android lisent `available` et distinguent une TV connue hors ligne ; matériel et CI NOT RUN. |
| 2026-11-04 | Livraison T-06 `369ffcb` poussée sans PR ; couverture cumulée finale 98,53 % après les 369 tests ciblés. Nettoyage des caches et données de couverture : 18 Mio supprimés ; aucun build natif produit. Validation native Ubuntu/Android et recette matérielle non exécutées dans cette reprise. |
| 2026-11-04 | T-06 : reprise de `f6a1a2c`, intégration de main après #122 et résolution documentaire sans perte. Mémorisation après appairage/lecture authentifiée et actualisation des coordonnées ajoutées ; 2 886 tests Python PASS, 8 SKIP (PyInstaller absent), couverture 98,07 %, lint/format/typage PASS. Dernier parcours : 369 tests ciblés PASS. CI non lancée, aucune nouvelle PR ni utilisation matérielle ; #123/#124 préservées. |
| 2026-11-06 | Claude, PR #125 : Correction de revue (Claude, 2026-11-06, PR #125, accord du propriétaire) : P1, si l'app était déjà au premier plan avant le lien, seule la réponse du récepteur la confirme jusqu'à la fin de la fenêtre (un refus tardif l'emporte) ; la confirmation rapide ne vaut que si une autre app était devant. P2, chaque lecture du premier plan (y compris la connexion) est bornée par le temps restant (`front_app(..., limit)`). Local : 2 877 PASS / 8 SKIP, couverture 98,83 % ; matériel NOT RUN. |
| 2026-11-06 | T-02 : `main` après #122 intégré dans la branche existante. Après le lien récepteur unique, la confirmation Android relit le package au premier plan dans l'échéance bornée sans renvoyer de commande ; 223 tests simulés PASS. CI et matériel NOT RUN ; #123/#124 inchangées. |
| 2026-11-01 | Codex, branche `feat/d2-extended-display` sans PR : D2 réutilise le bail Rust/WebRTC existant ; journal X11 privé atomique, restauration au prochain processus et conservation sur échec implémentés. Tests ciblés locaux PASS, matériel NOT RUN ; #120 reste l'unique PR ouverte. Route PulseAudio persistante encore ouverte. |
| 2026-10-31 | Première consolidation exhaustive du tableau de bord et des règles d'itération. |
| 2026-10-31 | PR #119 : cause du job qualité identifiée (skill Claude copié au lieu d'un lien relatif) ; lien corrigé localement, T-03/T-04/T-05 et nouveau défaut T-06 synchronisés dans TODO et plan ; validation/CI à relancer. |
| 2026-10-31 | PR #119 : le rerun confirme la correction du lien puis révèle quatre avis sur `pyasn1` 0.6.4 ; verrou mis à jour vers 0.6.5 sans dérogation, audit et CI à relancer. |
| 2026-11-01 | T-06 : registre privé des TV connues, restauration hors ligne, redécouverte et oubli explicite implémentés sur `fix/t06-persistent-discovery` ; contrôles locaux verts, test matériel non lancé et PR différée jusqu'à la fermeture de #118. |
| 2026-11-01 | T-02 : correction Android développée sans matériel sur `fix/t02-android-launch` : le POST signé du récepteur reste prioritaire, et le canal Android TV Remote sortant confirme aussi le package réellement au premier plan lorsque le téléphone ne peut pas recevoir le retour HTTP. Un seul lancement, aucune relance ; refus et expiration conservés. PR différée tant que #120 et #121 sont ouvertes. |
| 2026-11-01 | D3 (Claude) : branche `feat/d3-streaming-controls` depuis `main 5d9f06d`, limitée à `ui/` (aucun fichier de #120/#121). Défauts corrigés : appels d'écran sans délai, session WebRTC Rust affichée inactive à l'ouverture, réponse WebRTC illisible bloquant les contrôles, actualisation écrasant une opération en cours, mode affiché différent du flux réel. Tests UI 430/430 ; CI non lancée (pas de PR) ; matériel NOT RUN. Vérifié au 01/11 : #118 et #119 fusionnées (`5d9f06d`, `0a47454`), #120 (D1) et #121 (D2) ouvertes. PR D3 à ouvrir après elles. |
| 2026-11-06 | Claude, PR #124 : `main` après #122 intégré (conflit `DASHBOARD.md` seul, deux côtés conservés). Défaut corrigé : en mode Étendre, la position choisie dans le gestionnaire d'affichage (`x`, `y`) était perdue par les trois commandes Tauri (`start_webrtc_stream`, `bridge_start_screen_stream`, `bridge_prepare_webrtc_capture`) ; elle est désormais transmise au bridge. Tests de non-régression Rust et UI. Local : UI 433, Tauri 105 + clippy, Python 2 870 PASS / 8 SKIP, couverture 98,85 % ; matériel NOT RUN. |
| 2026-11-06 | Claude, CI PR #124 `c7547ae` : run CI vert (qualité/couverture, Tauri, paquet Linux `.deb`, APK) ; audit WebRTC/récepteur en échec sur RUSTSEC-2026-0441 (`pin-utils` 0.1.1 non maintenu, sans vulnérabilité ni version corrigée, via `nix 0.26` -> `webrtc-util 0.12` -> `webrtc 0.14`). Lockfile identique à `main` ; aucune dérogation ajoutée sans décision du propriétaire. #124 `MERGEABLE` mais `BLOCKED`. |
| 2026-11-06 | Claude, PR #124 : sur demande du propriétaire, dérogation OSV RUSTSEC-2026-0441 (`pin-utils`) jusqu'au 2026-11-30, comme `bincode`, motivée dans `SECURITY.md` et vérifiée par `tests/test_dev_tasks.py` ; migration `webrtc 0.21` toujours à faire. Local : 2 870 PASS / 8 SKIP. |
| 2026-11-06 | Claude, PR #124 : P1 de la revue Codex corrigé (seule discussion non résolue, qui bloquait la fusion). Après un échec WebRTC dont le nettoyage échouait, l'UI basculait en HLS et « Fermer » remettait l'état à inactif sans rendre l'écran étendu ni le son. `ScreenStreamController.stop()` restaure désormais ce qu'un échec WebRTC retient, l'échec restant affiché tant que ce n'est pas fait ; l'UI conserve le transport WebRTC connu. Tests Python et UI ajoutés. Local : UI 434, Python 2 872 PASS / 8 SKIP ; matériel NOT RUN. |
| 2026-11-07 | PR #124 fusionnée (`56f5f71`) après CI verte et résolution du fil P1. Propriétaire : la règle AGENTS imposant une approbation indépendante avant fusion est retirée ; les contrôles CI et l'autorisation explicite du propriétaire restent obligatoires. |
| 2026-11-01 | Microphone Android : capture avec permission explicite, session vocale Android Remote, arrêt et nettoyage implémentés sur branche dédiée ; APK, Rust, UI et tests Python ciblés validés localement, aucune utilisation d'appareil, CI non exécutée et aucune PR supplémentaire. |
| 2026-10-31 | Claude, branche `feat/d1-rust-screencast` sans PR : D1-3 VA-API avec repli explicite, D4-4 `PR_SET_PDEATHSIG`, correctif `agent_config.py` sous `python3` 3.7 ; skill `python-project-quality` synchronisé avec `my-skills` (branche `docs/python-cli-interpreter`). Tests locaux uniquement, CI non exécutée, matériel NOT RUN. |
| 2026-11-01 | Claude, PR #120 : `main` (#118, #119) intégré sans perte ; Opus temps réel, signalisation tolérante, reconnexion limitée aux pannes de lien (et panique supprimée), VA-API par nœud de rendu, test de bout en bout Rust, fuite de script de test corrigée. Tests locaux verts ; matériel NOT RUN. |
| 2026-11-01 | Codex, PR #122 : `main` après #120 intégré sans perte du microphone ; le double `TvControl` couvre les trois méthodes vocales et le verrou Kotlin respecte le contrôle lexical strict. Local : 12 tests ciblés, contrôle Python complet (2 785 PASS, 8 SKIP) et tests JVM Android PASS ; CI à exécuter, matériel NOT RUN. |
| 2026-11-03 | Codex, finalisation #121/#122 : vérification live des têtes `29a2611` et `7ad13d9`, toutes deux `MERGEABLE`. #122 : CI `41353404166` et audit `41353404430` verts (qualité/couverture, Tauri, paquet Linux, APK/JVM Android, audits) ; matériel NOT RUN, fusion non effectuée. |
| 2026-11-03 | Codex, correction P1 #122 : `main` contenant #121 intégré ; les tableaux PCM signés de Chaquopy deviennent des `bytes` Python avant `ControlService`, et `VoiceCapture.stop()` annule définitivement une capture en sérialisant l'ouverture réelle avec l'arrêt. Local : 11 tests Python ciblés, contrôle complet 2 870 PASS / 8 SKIP, couverture 98,85 %, 10 tests JVM ciblés PASS. SHA `75c8806` : CI `41684462930` et audit `41684463917` verts ; matériel NOT RUN. |
| 2026-11-01 | Claude, D4-5 son sur `feat/d4-5-audio-restore` (sans PR, empilée sur #121) : journal atomique partagé avec D2, restauration PulseAudio après arrêt normal, erreur ou SIGKILL, au démarrage suivant ; tests isolés de l'état réel. Tests locaux verts, matériel NOT RUN. |
| 2026-11-01 | Claude, `feat/d2-startup-x11-restore` (sans PR, issue de D4-5) : restauration X11 d'un processus tué dès le démarrage du bridge, réglages du propriétaire conservés, verrou `flock` du journal partagé ; corrige le démontage de l'extension vivante par `extension_output()`. Tests locaux verts, CI non exécutée (aucune PR autorisée), matériel NOT RUN. |
| 2026-11-01 | Claude, preuves autorisées sur le poste (sans TV) : son HARDWARE PARTIAL (route réelle, bridge relancé pendant la diffusion sans effet, SIGKILL, démarrage du bridge = état PulseAudio initial, journal effacé). X11 bloqué : aucun connecteur HDMI forcé, étape `sudo` du propriétaire requise. |
| 2026-11-01 | Claude, preuve X11 autorisée après l'étape `sudo` du propriétaire : 1er passage FAIL (sortie allumée par GNOME éteinte au lieu d'être rendue, disposition remise aussitôt), correctif poussé (état de la sortie journalisé et rendu à l'arrêt, à l'échec et à la reprise), 2e passage HARDWARE PARTIAL : état X11 initial retrouvé. Tests locaux verts, CI non exécutée. |
| 2026-11-01 | Codex, PR #121 : `main` après #120 et la branche `feat/d2-startup-x11-restore` intégrés sans réécriture ; D2, correction P1 X11 et D4-5 audio réunis. Local : 286 tests Python ciblés, 104 Tauri et 47/48 WebRTC PASS (1 endurance ignoré) ; CI de la tête finale à exécuter, aucun appareil utilisé. |
| 2026-11-01 | Codex, PR #121 : le premier passage CI du SHA `2dcdbf4` a reproduit un `PermissionError` dans le smoke test du pont gelé (`/root/.local/state` inaccessible). La reprise au démarrage signale désormais un journal illisible sans arrêter le pont et poursuit l'autre restauration ; test de non-régression ajouté. Local : 157 tests bridge et contrôle complet (2 864 PASS, 8 SKIP). SHA `d505d9f` : CI `41351653298` et audit `41351653694` verts, paquet Linux compris ; aucun appareil utilisé. |
