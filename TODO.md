# control-TV — TODO

État au 2026-10-13. Aujourd'hui : fin des implémentations ; essais sur TV et téléphone le 2026-10-14. Liste courte des prochaines actions, dans l'ordre. La référence complète reste [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) (section « Plan complet et suivi du temps ») et [DEVELOPMENT_CHECKLIST.md](DEVELOPMENT_CHECKLIST.md).

**Objectif :** deux télécommandes autonomes, Ubuntu et Android, qui contrôlent la même Google TV en parallèle, sans serveur permanent. MCP, ChatGPT Voice et Windows sont abandonnés depuis le 2026-10-12.

**Règles :** aucune fusion et aucune commande envoyée à la TV sans l'accord explicite du propriétaire. Une CI verte n'est pas une validation sur matériel.

## 1. Fusions — terminé le 2026-10-12

- [x] **#41, #42, #43** : canal télécommande dans le `.deb` et l'APK, clavier distant, nettoyage du plan. Fusionnées.
- [x] **#38** : tests de convergence Ubuntu/Android. Corrections P3 déjà faites ; branche remise à jour avec `main`, puis fusionnée (CI `main` 4/4 verte).
- [x] Code MCP de `main` (PR #31) : **conservé tel quel**, sans développement (décision du propriétaire).
- [x] **#33** (widget Android) et **#49 à #53** (corrections de l'audit de `main`) : fusionnées le 2026-10-13. Seule PR ouverte : la remise en place de la documentation de #50 (annulée par #53 le temps de fusionner #33).

## 1 bis. Qualité du code — terminé le 2026-10-13

- [x] Compétence `code-solid` appliquée ; plus de `Any` ni de `unknown` dans le code (les tests peuvent en garder) ; mypy refuse désormais tout `Any` explicite hors tests.

## 1 ter. Corrections de l'audit de `main` (2026-10-13)

- [x] **P3-3** : message d'erreur ADB — code `debugging_off` distinct, aide « débogage USB » seulement dans ce cas ; tests Python et UI. Fusionnée (#49).
- [ ] **P2-1, P3-1, P3-2** : README, ARCHITECTURE et TODO actualisés (MCP / Voice / Windows abandonnés, icône système fusionnée) — fusionnée (#50) avant #33 par erreur, annulée (#53), remise en place après #33 (PR en cours).
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

- [ ] **Refonte de l'interface (demande du 2026-10-13)** : télécommande d'abord (pavé directionnel rond, volume TV en bascule, Retour/Accueil), applications de la TV en grille de tuiles, clavier repliable, son Cast en dernier ; thème clair/sombre selon le système, cibles tactiles ≥ 48 px, contrastes AA. Branche `feat/ui-redesign`, vérifiée sur captures (TV fictive) ; à voir sur le téléphone et sur Ubuntu.
- [ ] Installer le `.deb` et l'APK finaux sur le PC et le téléphone, puis refaire la recette complète.
- [ ] Documentation d'utilisation : appairage, clavier, limites (par exemple, cette TV ne rapporte pas son volume).
- [ ] Icône dans la barre système Ubuntu (élément K) : **fusionnée** (#46, 2026-10-13), pas encore vue sur un vrai bureau — Ouvrir, Lecture/Pause, Stop, Mute TV, Volume TV −/+, Quitter ; fermer la fenêtre quitte l'application (décision à confirmer). À voir sur ton bureau Ubuntu.

## Suivi

| Lot | Avancement | Restant estimé |
|---|---|---|
| Phase 1 : autonomie et convergence | tests simulés 100 %, fusionnés (#38) ; matériel partiel, non classé | 14–28 h (enveloppe) |
| Phase 2 : commandes, applications, clavier | son validé sur Ubuntu ; le reste codé, 0 % confirmé sur TV ; applications 0 % | 16–32 h, à réestimer après la décision ADB |
| Phase 3 : livraison | builds CI disponibles ; recette finale à faire | 10–20 h |
