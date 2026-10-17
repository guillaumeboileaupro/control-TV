# control-TV — TODO

## Temps restant consolidé — 2026-10-17 (instantané vérifié à 21 h 35 CEST)

**Référence Git :** `main` `de31c94`, CI `39034444658` verte. Les PR #79, #81, #82,
#83, #84 et #85 sont ouvertes et aucune n'est comptée comme intégrée. Les durées ci-dessous sont
des heures de travail, pas des délais calendaires. L'estimation centrale est le milieu de la plage,
pas un pourcentage d'avancement.

### État vérifié des PR ouvertes

| PR / SHA | État démontré | Revue / blocage | CI |
|---|---|---|---|
| #79 `c7573dd` | Architecture D0 et essai VA-API CQP documentés | Revue indépendante à refaire : la revue automatique a atteint sa limite et n'a produit aucun avis exploitable | 4/4 verte, run `39036089640` |
| #81 `3ba057e` | Socle D0 synthétique, 138 tests screencast ; les anciens P1 `reclaim()` / `close()` sont corrigés | Revue indépendante Codex terminée sans anomalie majeure ; fusion soumise à l'accord du propriétaire | 4/4 verte, run `39045172292` ; 1 727 tests, 9 ignorés, couverture 99,13 % |
| #82 `0e1507c` avant la présente mise à jour | Planning documentaire | Deux P2 documentaires de la première revue sont traités par le présent tableau et la suppression des estimations concurrentes | 4/4 verte sur l'ancien SHA, run `39043825272` ; nouvelle CI requise |
| #83 `fedda7f` | Fondations D1 abstraites, sans capture ni réseau ; 56 tests ciblés et couverture du module 100 % | Deux P2 ouverts : `close()` concurrent peut être perdu ; une interruption pendant le nettoyage peut laisser des ressources | 4/4 verte, run `39046537868` |
| #84 `a83f139` | Provenance des paquets, préparation de recette et signature Android stable préparées | Un P1 ouvert (lier l'APK à son manifeste) et un P2 ouvert (hôte Ubuntu sans installation préalable) ; nouvelle revue ensuite | 4/4 verte, run `39049910715` |
| #85 `6fa6970` | Trace applicative C6/C7 et tests simulés présents | Deux P1 ouverts (observation C6 indépendante ; corrélation avec l'ID du bridge) et un P2 (preuve absente doit rester BLOCKED) | 4/4 verte, run `39062555039` |

### Budget unique restant

| Chantier | État vérifié | Min. | Max. | Centre | Blocage / dépendance | Prochaine action | Responsable |
|---|---|---:|---:|---:|---|---|---|
| D0 — H.264 et validation | Développement terminé dans #81 ; revue #81 terminée ; invocation locale VA-API/libx264 du service et revue #79 encore ouvertes | 1 h | 3 h | 2 h | Accord de fusion ; matériel GPU local non validé par #81 | Revoir #79, fusionner #79/#81 avec accord, puis essai synthétique local borné | Claude (code), Codex (revue), propriétaire (fusion/essai) |
| D1 — duplication Ubuntu vers Freebox | En cours : contrats et cycle de vie simulé dans #83 ; aucun adaptateur concret, capture, réseau ou résultat Freebox | 18 h | 31 h | 24,5 h | Corriger les deux P2 de #83 ; dépend de D0 ; compatibilité HLS Freebox inconnue | Corriger/revoir #83, adapter D0, implémenter le MVP HLS, puis spike récepteur et capture autorisés | Codex, revue Claude, propriétaire pour le matériel |
| D2 — extension du bureau | Non commencé | 12 h | 24 h | 18 h | Dépend de D1 et des capacités X11/i915 ; modification X11 soumise à accord | Spike d'écran virtuel réversible, puis diffusion de ce seul écran | Codex, revue Claude, propriétaire |
| D3 — interface screencast | Non commencé | 8 h | 12 h | 10 h | Dépend des contrats stabilisés D1/D2 | Concevoir puis tester Dupliquer / Étendre / Arrêter et les états associés | Codex, revue Claude |
| D4 — sécurité screencast | Non commencé ; exigences documentées | 6 h | 10 h | 8 h | Dépend de D1–D3 ; n'inclut ni les essais de réception D1 ni la recette télécommande | Revue sécurité, consentement, confinement LAN, nettoyage et RD1–RD8 hors essais déjà comptés | Codex + revue indépendante + propriétaire |
| C6/C7 — développement, revue et simulation | En correction dans #85 ; simulation présente mais insuffisante tant que les 2 P1 et le P2 restent ouverts | 3 h | 6 h | 4,5 h | Réconcilier #85 avec #84 ; préserver ID bridge, observateur indépendant et verdict BLOCKED sans preuve | Corriger #85, tests de non-régression, rebase après #84, nouvelle revue | Codex, revue Claude |
| Packaging et préparation de recette | En cours dans #84 | 3 h | 6 h | 4,5 h | Deux avis ouverts ; le propriétaire doit créer/sauvegarder la clé Android et publier son certificat | Corriger #84, revue, configurer la clé, vérifier provenance `.deb`/APK et finaliser la documentation | Claude, propriétaire, revue Codex |
| Recette matérielle télécommandes | Fonctionnalités implémentées ; validations réelles majoritairement en attente | 6,5 h | 11 h | 8,75 h | Dépend de #84 et #85 corrigées/fusionnées, des artefacts identifiés et des autorisations par étape | Exécuter le protocole manuel et produire le rapport final | Propriétaire + Codex pour les preuves |
| **TOTAL UNIQUE** | **D0–D4 + C6/C7 hors matériel + packaging + recette matérielle** | **57,5 h** | **103 h** | **80,25 h** | **Réestimer D1 si HLS échoue ; Cast Streaming (20–40 h) ou Sunshine/Moonlight (8–16 h) sont des alternatives, jamais des budgets additionnés** | **Suivre l'ordre de fusion ci-dessous** | — |

### Détail non additionnel de la recette matérielle (inclus dans les 6,5–11 h)

| Preuve à obtenir | État vérifié | Min. | Max. | Centre | Blocage / dépendance | Prochaine action | Responsable |
|---|---|---:|---:|---:|---|---|---|
| Ubuntu réel | Installation/lancement déjà observés sur un ancien build ; recette finale ouverte | 0,75 h | 1,25 h | 1 h | #84 corrigée, `.deb` et provenance exacts, accord d'installation | Lancement, IPC, tray, fermeture et journaux | Propriétaire + Codex |
| Android réel | Application construite ; commandes matérielles non validées | 1 h | 1,75 h | 1,375 h | #84 corrigée, APK identifié/signé, accord d'installation | Installer sans perdre les données, lancer et relever les journaux | Propriétaire + Codex |
| Widget | Implémenté et partiellement essayé ; scénarios de reprise ouverts | 0,5 h | 1 h | 0,75 h | Android installé avec signature stable | Reprise de processus, Wi-Fi, batterie, notification et verrou multicast | Propriétaire + Codex |
| Applications installées | Liste Ubuntu observée ; lancement et Android ouverts | 0,5 h | 0,75 h | 0,625 h | ADB uniquement après accord | Lister et lancer depuis les deux clients | Propriétaire + Codex |
| Clavier distant | Développement terminé, matériel non validé | 0,25 h | 0,5 h | 0,375 h | Champ TV disponible et autorisation de commande | Texte, effacement et validation | Propriétaire + Codex |
| Alimentation | Développement terminé, matériel non validé | 0,25 h | 0,5 h | 0,375 h | Veille/réveil dépendants du matériel | Tester veille et réveil séparément | Propriétaire + Codex |
| Commandes simultanées C1–C5 | Simulation terminée ; matériel non validé | 0,75 h | 1,25 h | 1 h | Deux clients installés et autorisations par étape | Exécuter les commandes, vérifier convergence et absence de rejeu | Propriétaire + Codex |
| Latence K1–K3 | Instrumentation statistique présente ; aucune mesure matérielle | 0,75 h | 1,25 h | 1 h | Seuils K1/K2 à faire adopter par le propriétaire | Filmer/mesurer puis classer sans seuil inventé | Propriétaire + Codex |
| C6/C7 matériel | Non exécuté | 0,75 h | 1,25 h | 1 h | #85 corrigée/revue, observation indépendante et interruption autorisée | Exécuter une fois chaque scénario et conserver la trace | Propriétaire + Codex |
| Rapport et documentation | Non commencé pour la campagne finale | 1 h | 1,5 h | 1,25 h | Dépend de tous les résultats ci-dessus | Consolider preuves, FAIL/BLOCKED/NOT RUN et versions exactes | Codex, revue Claude |

**Absence de double comptage :** les essais de réception et de capture appartiennent à D1, les
essais matériels C6/C7 à la recette de 6,5–11 h, les corrections/revues de #85 au lot C6/C7 de
3–6 h, et D4 couvre uniquement la sécurité/validation du partage d'écran. Le packaging prépare les
artefacts ; leur installation et leur manipulation devant les appareils appartiennent à la recette.

**Ordre de fusion recommandé :** #79, #81, #84 après ses corrections, #83 après ses corrections et
rebase sur #81, #85 après ses corrections et rebase sur #84, puis #82 remise à jour avec `main` en
dernier. #82 est documentaire mais touche `TODO.md` et `DEVELOPMENT_PLAN.md`, également modifiés par
#83–#85 ; la fusion en dernier évite de perdre leur historique.

**Sécurité et consentement :** aucune fusion, installation, capture, activation ADB, commande TV ou
test matériel sans l'autorisation expresse correspondante. Une CI verte prouve l'automatisation du
commit testé, jamais le comportement du matériel.

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

### B. Anomalie visuelle : logos et grille d'applications — priorité immédiate

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

Plan détaillé, commandes et autorisations : [`docs/HARDWARE_ACCEPTANCE_PLAN.md`](docs/HARDWARE_ACCEPTANCE_PLAN.md) (séances A à D, 6–10 h avec le propriétaire). Constat réel déjà acquis : la liste des 28 applications s'affiche sur Ubuntu (débogage activé sur la Freebox, captures du 2026-10-17).


- [ ] Ubuntu : autorisation explicite avant toute installation du `.deb` ; lancement réel, bridge IPC, icône système, logs, fermeture et CSP.
- [ ] Android : autorisation explicite avant installation de l'APK ; lancement, affichage, widget, CSP et journaux ; préserver les données existantes.
- [ ] Google TV : obtenir l'accord explicite du propriétaire **avant toute exécution**, y compris R1–R4 bien qu'ils soient en lecture seule ; obtenir également cet accord avant chaque étape C1–C7 et avant toute autre commande TV. Toute activation ADB exige elle aussi un accord explicite préalable. Exécuter ensuite, selon `docs/DUAL_CONTROLLER_VALIDATION.md`, R1–R4, C1–C7, TV-POWER, TV-KEYBOARD, TV-APPS, TV-ERRORS et la concurrence des deux télécommandes.
- [ ] Latence : K1–K3 et `latency.remote-keys`, mesures vidéo et seuils à adopter par le propriétaire.
- [ ] Distinguer systématiquement PASS simulé, PASS CI et PASS matériel ; rapport de preuves par scénario.
- [ ] Mettre à jour `DEVELOPMENT_PLAN.md`, `DEVELOPMENT_CHECKLIST.md`, README et rapports à l'issue des essais.

### D. Nouvelle fonctionnalité après recette : affichage Ubuntu X11 vers Google TV

- [x] **Étude de faisabilité (2026-10-17, documentation seulement) :** [`docs/REMOTE_DISPLAY_STUDY.md`](docs/REMOTE_DISPLAY_STUDY.md) — récepteurs (HLS vers le récepteur Cast par défaut, protocole de recopie Cast Streaming, récepteur web dédié, Sunshine/Moonlight, VNC), capture et encodage, écrans virtuels X11 (evdi, têtes virtuelles du pilote, `xrandr --setmonitor`), architecture et interfaces, sécurité, critères RD1–RD8, risques et estimations.
- [x] **Phase D0 — développement automatisé :** PR #81, encodeur H.264 synthétique borné, VA-API CQP / libx264, cycle de vie et nettoyage ; revue indépendante terminée au SHA `3ba057e`. La validation locale du service avec FFmpeg/GPU reste ouverte et n'est pas une validation Freebox.
- [ ] **Phase D1 — duplication :** PR #83 fournit les contrats et le cycle de vie simulé, mais ses deux P2 de revue restent à corriger ; aucun adaptateur de capture, encodage D0, publication ou réception réel n'est encore intégré. Après correction : MVP HLS, capture X11 consentie, diffusion Freebox et mesures.
- [ ] **Phase D2 — extension :** étudier et tester un véritable affichage virtuel X11 indépendant, son intégration avec le gestionnaire de fenêtres et la diffusion du seul écran virtuel ; vérifier les capacités du GPU/driver Dell.
- [ ] **Phase D3 — intégration UI :** actions explicites « Dupliquer », « Étendre », « Arrêter » ; sélection TV/écran, résolution et FPS ; aucun lancement automatique de capture.
- [ ] **Phase D4 — sécurité et validation :** consentement explicite à la capture, arrêt et nettoyage garantis, LAN/authentification, aucune exposition non voulue, tests Ubuntu/TV réels, documentation et rapports.
- [x] Le propriétaire a explicitement repriorisé D0 puis la préparation D1 avant la clôture de la recette ; les opérations matérielles restent soumises à des accords séparés.
- **Dépendances et priorités actuelles :** #79 puis #81 ; correction de #83 et intégration D0 ; choix du récepteur après preuve Freebox ; D1 ; D2 ; D3 ; D4. La recette télécommande et le packaging suivent leur chaîne séparée #84 → #85, sans être recomptés dans D4.
- **Estimations :** le tableau consolidé en tête de ce fichier est l'unique estimation courante. Les anciennes plages de cette section sont conservées dans l'historique Git et ne doivent plus être additionnées.
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
| Power (veille / réveil) | codé, 0 % confirmé | codé, 0 % confirmé | essai |
| Flèches, OK, Retour, Accueil | codé, 0 % confirmé | codé, 0 % confirmé | essai, et mesure de la latence par touche (étapes K1–K3 de `docs/DUAL_CONTROLLER_VALIDATION.md`, vidéo) |
| Clavier distant (texte, Backspace, Entrée) | codé (#42), 0 % confirmé | codé (#42), 0 % confirmé | essai : TV allumée, recherche YouTube ouverte |
| Application active affichée | codé (#42), 0 % confirmé | codé (#42), 0 % confirmé | essai |
| Applications installées : liste et lancement | codé, 0 % confirmé | codé, 0 % confirmé | essai : activer le débogage USB sur la TV, autoriser control-TV, lister, ouvrir |
| Lecture, pause, stop, seek | partiellement observé | 0 % | essais C1–C7 autorisés |

- [ ] **Critère de latence des touches — ta décision :** proposé (non adopté) : délai envoi → effet médian ≤ 0,5 s et maximal ≤ 1,0 s, délai envoi → télécommande prête médian ≤ 1,0 s, sur Ubuntu et sur Android. Tant qu'il n'est pas adopté, K1 et K2 mesurent sans juger la latence.
- [ ] **Téléphone :** le brancher en USB, accepter le débogage, et autoriser l'installation de l'APK de la CI. Si les signatures diffèrent, le widget actuel sera perdu.
- [ ] **Applications installées (2E) — à essayer :** la liste passe par le débogage de la TV (ADB), **désactivé tant que tu ne l'actives pas** : Paramètres › Système › À propos › « Build de l'OS Android TV » 7 fois, puis Options pour les développeurs › Débogage USB. La première fois, la TV demande d'autoriser control-TV. **Si ta TV ne propose que « Débogage sans fil » (avec un code d'appairage), la liste ne marchera pas encore** : c'est le premier point à vérifier demain. L'ouverture d'une application passe aussi par ADB : le débogage doit rester actif pour ouvrir les applications depuis control-TV.
- [ ] Commandes concurrentes C1–C7 depuis Ubuntu et Android, avec autorisation.

## 4. Widget Android — PR #33 (fusionnée le 2026-10-13)

- [x] Conflits résolus et branche remise à jour avec `main` (2026-10-13, CI 4/4 verte) ; description de la PR actualisée ; revue Codex demandée.
- [x] Documentation du Mute corrigée : mute Cast confirmé par le récepteur, volume fixe, aucun effet audible (le vrai mute passe par le canal télécommande).
- [ ] Tester sur le téléphone, avec ton accord pour chaque commande : Lecture/Pause, Stop, Volume.
- [ ] Suite possible : faire passer le Mute du widget par le canal télécommande (le son réellement entendu).
- [x] Fusion de la #33 : faite le 2026-10-13, avant les essais sur téléphone (`1b86f47`). Mute sur volume Cast fixe affiché « Muted (Cast) · TV sound may not change ».

## 5. Phase 3 — livraison

- [ ] **Refonte de l'interface (demande du 2026-10-13)** : télécommande d'abord (pavé directionnel rond, volume TV en bascule, Retour/Accueil), applications de la TV en grille de tuiles, clavier repliable, son Cast en dernier ; thème clair uniquement, cibles tactiles ≥ 48 px, contrastes AA. Branche `feat/ui-redesign`, vérifiée sur captures (TV fictive) ; à voir sur le téléphone et sur Ubuntu.
- [ ] Installer le `.deb` final sur le PC et l'APK final sur le téléphone, puis refaire la recette complète. La CI a validé construction/installation/test/purge du `.deb`, mais aucune installation locale de l'artefact `d95fc7b` ni recette sur TV n'a été faite.
- [ ] Documentation d'utilisation : appairage, clavier, limites (par exemple, cette TV ne rapporte pas son volume).
- [ ] Icône dans la barre système Ubuntu (élément K) : **fusionnée** (#46, 2026-10-13), pas encore vue sur un vrai bureau — Ouvrir, Lecture/Pause, Stop, Mute TV, Volume TV −/+, Quitter ; fermer la fenêtre quitte l'application (décision à confirmer). À voir sur ton bureau Ubuntu.

## Suivi

| Lot | Avancement | Restant estimé |
|---|---|---|
| Phase 1 : autonomie et convergence | tests simulés 100 %, fusionnés (#38) ; matériel partiel, non classé | 14–28 h (enveloppe) |
| Phase 2 : commandes, applications, clavier | son validé sur Ubuntu ; le reste codé, 0 % confirmé sur TV ; applications 0 % | 16–32 h, à réestimer après la décision ADB |
| Phase 3 : livraison | CI `d95fc7b` verte, APK et `.deb` construits, `.deb` installé/testé/purgé en CI ; recette matérielle finale à faire | 10–20 h |

**Total réaliste restant : 40–80 h** selon la disponibilité du téléphone et de la TV, les autorisations de commandes, le comportement ADB du téléviseur et les corrections révélées par la recette. Ce temps concerne principalement les validations physiques et leurs éventuelles reprises, pas les neuf corrections d'audit déjà fusionnées.
