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
| PR actives vérifiées au 01/11 | #118, #119 et **#120** fusionnées ; **#121** D2 et **#122** microphone ouvertes. #121 intègre `main` et les corrections X11/audio ; nouvelle CI à exécuter |
| Défauts P1 observés | 3 : appairage durable, découverte Chambre, lancement Android Liberté |
| Défaut P2 observé | Assistant sans effet sur Liberté |
| Deux chantiers structurants | **D1 duplication** et **D2 extension**, à terminer et valider séparément |
| Contrôles techniques | Rust/WebRTC, widget Android et menu GNOME testés automatiquement, mais pas totalement sur matériel |
| Nettoyage local rapporté | 3 worktrees + 4 branches locales de PR supprimés, anciens dist réduits à 435 Mio ; nouveaux APK de recette à préserver |
| Sources privées | `.ai-private/hardware/recette-2026-10-26/`, `.ai-private/hardware/recette-2026-10-28/` ; rapport Claude privé |

## Tableau exhaustif des travaux restants

Estimations min / max / médiane en **heures**, à réviser après chaque livraison.

| Priorité | Chantier | État | Temps restant (min / max / méd.) | Prochaine action | Responsable |
|---|---|---|---|---|---|
| P1 | T-01 - Appairage durable | #118 ouverte ; FAIL matériel avant migration | 0,5 / 2 / 1 h | Finaliser #118, revue, fusion, recette | Codex / Claude |
| P1 | T-06 - Découverte persistante | Chambre FAIL 3/3 | 2 / 5 / 3 h | Garder TV appairée visible et reconnectable | Codex |
| P1 | T-02 - Applications Android | Liberté FAIL 4/4 ; Ubuntu PASS | 2 / 5 / 3 h | Relever version récepteur et corriger | Codex |
| P1 | D1 - Duplication Rust/WebRTC/HLS | MERGED via #120 : pertes sans corruption, VA-API avec repli `libx264`, Opus temps réel, signalisation tolérante, reconnexion limitée aux pannes de lien et test Rust de bout en bout. HARDWARE NOT RUN | 3 / 8 / 5 h | D1-9 avec autorisation | Claude |
| P1 | D2 - Extension Rust/X11 | PR #121 : connecteur RandR forcé conservé sans evdi ; journal X11 atomique et reprise après plantage implémentés/testés. HARDWARE NOT RUN | 4 / 12 / 7 h | Intégrer la correction P1 de restauration, CI puis recette autorisée | Codex |
| P1 | D3 - Interface de diffusion | Auto/WebRTC/HLS codés ; recette incomplète | 1 / 3 / 2 h | Valider Dupliquer/Étendre/Arrêter | Claude |
| P1 | D4 - Robustesse | MERGED : arrêt, reconnexion bornée, récolte FFmpeg et D4-4 via #120. PR #121 : D4-5 X11 et son intégrés, journal partagé, reprise au démarrage après SIGKILL, réglages utilisateur conservés et verrou protégeant une diffusion vivante. HARDWARE PARTIAL 01/11 : son et X11 restaurés séparément sur le poste, sans TV ni application installée | 2 / 5 / 3 h | CI #121 puis recette autorisée SIGKILL pendant une diffusion avec son | Codex / Claude |
| P1 | Récepteur Android TV | Code présent, 2 TV non validées | 2 / 6 / 3 h | Décodage, audio, plein écran, fermeture | Claude |
| P1 | Audio / synchronisation | Opus/PulseAudio codés ; physique non validé | 1 / 4 / 2 h | Mesurer synchronisation sur TV | Claude |
| P1 | Latence / performances | HLS ancien 9-11 s ; WebRTC <500 ms non prouvé | 1 / 4 / 2 h | Mesurer médiane, p95, pire, FPS, CPU/GPU | Claude |
| P1 | Skill sécurité dépendances | dependency-update-review existe, nouveau skill manquant | 1 / 3 / 2 h | my-skills puis control-TV | Codex |
| P1 | Recette Chambre / Liberté | T-03/04/05 PASS ciblés ; reste incomplet | 2 / 6 / 4 h | Tests réels autorisés, scénario par scénario | Claude |
| P1 | Documentation TODO / dashboard | Synchronisation continue nécessaire | 0,5 / 1,5 / 1 h | Actualiser exhaustivement à chaque fin de mission | Agent actif |
| P2 | Widget Android 4x4 | Développé, recette incomplète | 0,5 / 2 / 1 h | Tests tactiles réels | Claude |
| P2 | Menu GNOME | Développé, recette incomplète | 0,5 / 2 / 1 h | Tests sur Ubuntu | Claude |
| P2 | Assistant / microphone | Assistant sans effet sur Liberté | 2 / 6 / 3 h | Clarifier capacités et chemin vocal | Codex |
| P2 | Logos / langues / Power-CEC | Non repris point par point le 30/10 | 1 / 3 / 2 h | Tests ciblés et limites TV | Codex / Claude |
| P2 | CI / sécurité / maintenance / nettoyage | `pyasn1` 0.6.5 fusionné via #119 ; anciennes dérogations WebRTC à revoir avant 30/11 | 0,5 / 2 / 1 h | Audits et nettoyage après chaque livraison | Agent actif |
| **TOTAL** | **Travaux restants estimés** | **Ne comprend pas de nouvelle anomalie majeure** | **26 / 79,5 / 46 h** | **Réviser après vérification** | **Coordinateur** |

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
| 4 | Codex | Découverte persistante, puis lancement Android | Une PR P1 à la fois |
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
| 2026-11-01 | Codex, branche `feat/d2-extended-display` sans PR : D2 réutilise le bail Rust/WebRTC existant ; journal X11 privé atomique, restauration au prochain processus et conservation sur échec implémentés. Tests ciblés locaux PASS, matériel NOT RUN ; #120 reste l'unique PR ouverte. Route PulseAudio persistante encore ouverte. |
| 2026-10-31 | Première consolidation exhaustive du tableau de bord et des règles d'itération. |
| 2026-10-31 | PR #119 : cause du job qualité identifiée (skill Claude copié au lieu d'un lien relatif) ; lien corrigé localement, T-03/T-04/T-05 et nouveau défaut T-06 synchronisés dans TODO et plan ; validation/CI à relancer. |
| 2026-10-31 | PR #119 : le rerun confirme la correction du lien puis révèle quatre avis sur `pyasn1` 0.6.4 ; verrou mis à jour vers 0.6.5 sans dérogation, audit et CI à relancer. |
| 2026-10-31 | Claude, branche `feat/d1-rust-screencast` sans PR : D1-3 VA-API avec repli explicite, D4-4 `PR_SET_PDEATHSIG`, correctif `agent_config.py` sous `python3` 3.7 ; skill `python-project-quality` synchronisé avec `my-skills` (branche `docs/python-cli-interpreter`). Tests locaux uniquement, CI non exécutée, matériel NOT RUN. |
| 2026-11-01 | Claude, PR #120 : `main` (#118, #119) intégré sans perte ; Opus temps réel, signalisation tolérante, reconnexion limitée aux pannes de lien (et panique supprimée), VA-API par nœud de rendu, test de bout en bout Rust, fuite de script de test corrigée. Tests locaux verts ; matériel NOT RUN. |
| 2026-11-01 | Claude, D4-5 son sur `feat/d4-5-audio-restore` (sans PR, empilée sur #121) : journal atomique partagé avec D2, restauration PulseAudio après arrêt normal, erreur ou SIGKILL, au démarrage suivant ; tests isolés de l'état réel. Tests locaux verts, matériel NOT RUN. |
| 2026-11-01 | Claude, `feat/d2-startup-x11-restore` (sans PR, issue de D4-5) : restauration X11 d'un processus tué dès le démarrage du bridge, réglages du propriétaire conservés, verrou `flock` du journal partagé ; corrige le démontage de l'extension vivante par `extension_output()`. Tests locaux verts, CI non exécutée (aucune PR autorisée), matériel NOT RUN. |
| 2026-11-01 | Claude, preuves autorisées sur le poste (sans TV) : son HARDWARE PARTIAL (route réelle, bridge relancé pendant la diffusion sans effet, SIGKILL, démarrage du bridge = état PulseAudio initial, journal effacé). X11 bloqué : aucun connecteur HDMI forcé, étape `sudo` du propriétaire requise. |
| 2026-11-01 | Claude, preuve X11 autorisée après l'étape `sudo` du propriétaire : 1er passage FAIL (sortie allumée par GNOME éteinte au lieu d'être rendue, disposition remise aussitôt), correctif poussé (état de la sortie journalisé et rendu à l'arrêt, à l'échec et à la reprise), 2e passage HARDWARE PARTIAL : état X11 initial retrouvé. Tests locaux verts, CI non exécutée. |
| 2026-11-01 | Codex, PR #121 : `main` après #120 et la branche `feat/d2-startup-x11-restore` intégrés sans réécriture ; D2, correction P1 X11 et D4-5 audio réunis. Tests/CI de la tête finale à exécuter ; aucun appareil utilisé. |
