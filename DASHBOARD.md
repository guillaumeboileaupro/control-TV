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
| PR active connue au 31/10 | **#118** - migration des anciens certificats ; HEAD vérifié `0a19fd7` |
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
| P1 | D1 - Duplication Rust/WebRTC/HLS | Code avancé ; matériel non accepté | 3 / 8 / 5 h | Achever parcours capture -> TV | Claude |
| P1 | D2 - Extension Rust/X11 | Virtuel partiel ; vrai second écran non validé | 4 / 12 / 7 h | Bureau indépendant et restauration X11 | Claude |
| P1 | D3 - Interface de diffusion | Auto/WebRTC/HLS codés ; recette incomplète | 1 / 3 / 2 h | Valider Dupliquer/Étendre/Arrêter | Claude |
| P1 | D4 - Robustesse | Automatisation existante, recette incomplète | 2 / 5 / 3 h | Arrêt, reconnexion, cleanup et restauration | Claude |
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
| P2 | CI / sécurité / maintenance / nettoyage | Suivi continu ; anciennes dérogations WebRTC à revoir avant 30/11 | 0,5 / 2 / 1 h | Audits et nettoyage après chaque livraison | Agent actif |
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
| 2026-10-31 | Première consolidation exhaustive du tableau de bord et des règles d'itération. |
