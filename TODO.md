# control-TV — TODO

État au 2026-10-13. Aujourd'hui : fin des implémentations ; essais sur TV et téléphone le 2026-10-14. Liste courte des prochaines actions, dans l'ordre. La référence complète reste [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) (section « Plan complet et suivi du temps ») et [DEVELOPMENT_CHECKLIST.md](DEVELOPMENT_CHECKLIST.md).

**Objectif :** deux télécommandes autonomes, Ubuntu et Android, qui contrôlent la même Google TV en parallèle, sans serveur permanent. MCP, ChatGPT Voice et Windows sont abandonnés depuis le 2026-10-12.

**Règles :** aucune fusion et aucune commande envoyée à la TV sans l'accord explicite du propriétaire. Une CI verte n'est pas une validation sur matériel.

## 1. Fusions — terminé le 2026-10-12

- [x] **#41, #42, #43** : canal télécommande dans le `.deb` et l'APK, clavier distant, nettoyage du plan. Fusionnées.
- [x] **#38** : tests de convergence Ubuntu/Android. Corrections P3 déjà faites ; branche remise à jour avec `main`, puis fusionnée (CI `main` 4/4 verte).
- [x] Code MCP de `main` (PR #31) : **conservé tel quel**, sans développement (décision du propriétaire).
- Seule PR encore ouverte : **#33** (widget Android, section 4).

## 1 bis. Qualité du code — terminé le 2026-10-13

- [x] Compétence `code-solid` appliquée ; plus de `Any` ni de `unknown` dans le code (les tests peuvent en garder) ; mypy refuse désormais tout `Any` explicite hors tests.

## 1 ter. Corrections de l'audit de `main` (2026-10-13)

- [ ] **P3-3** : message d'erreur ADB — code `debugging_off` distinct, aide « débogage USB » seulement dans ce cas ; tests Python et UI. PR ouverte, fusion sur ton accord.
- [ ] **P2-1, P3-1, P3-2** : README, ARCHITECTURE et TODO actualisés (MCP / Voice / Windows abandonnés, icône système fusionnée) — PR documentaire.
- [ ] **P3-4** : le bridge dépend du port `TvControl` complété plutôt que du `ControlService` concret — PR distincte.
- [ ] **P3-6** : mesurer la latence des touches (flèches) pendant la recette matérielle.

## 2. Phase 1 — les deux applications sur la même TV

- [ ] **R1 à R4** (lecture seule) : Ubuntu et le téléphone ouverts en même temps, sur la même TV. Résultat PASS/FAIL/NOT RUN pour chaque test. Avancement : partiel, non classé.
- [ ] Vérifier sur Android que la découverte signale bien le canal télécommande de la TV.

## 3. Phase 2 — essais sur la TV (avec autorisation, propriétaire devant la TV)

| Fonction | Ubuntu | Android | Ce qui manque |
|---|---|---|---|
| Volume +/−, Mute (son réel) | ✅ validé le 2026-10-12 | codé, 0 % confirmé | installer l'APK de #41 |
| Power (veille / réveil) | codé, 0 % confirmé | codé, 0 % confirmé | essai |
| Flèches, OK, Retour, Accueil | codé, 0 % confirmé | codé, 0 % confirmé | essai |
| Clavier distant (texte, Backspace, Entrée) | codé (#42), 0 % confirmé | codé (#42), 0 % confirmé | essai : TV allumée, recherche YouTube ouverte |
| Application active affichée | codé (#42), 0 % confirmé | codé (#42), 0 % confirmé | essai |
| Applications installées : liste et lancement | codé, 0 % confirmé | codé, 0 % confirmé | essai : activer le débogage USB sur la TV, autoriser control-TV, lister, ouvrir |
| Lecture, pause, stop, seek | partiellement observé | 0 % | essais C1–C7 autorisés |

- [ ] **Téléphone :** le brancher en USB, accepter le débogage, et autoriser l'installation de l'APK de la CI. Si les signatures diffèrent, le widget actuel sera perdu.
- [ ] **Applications installées (2E) — à essayer :** la liste passe par le débogage de la TV (ADB), **désactivé tant que tu ne l'actives pas** : Paramètres › Système › À propos › « Build de l'OS Android TV » 7 fois, puis Options pour les développeurs › Débogage USB. La première fois, la TV demande d'autoriser control-TV. **Si ta TV ne propose que « Débogage sans fil » (avec un code d'appairage), la liste ne marchera pas encore** : c'est le premier point à vérifier demain. L'ouverture d'une application passe aussi par ADB : le débogage doit rester actif pour ouvrir les applications depuis control-TV.
- [ ] Commandes concurrentes C1–C7 depuis Ubuntu et Android, avec autorisation.

## 4. Widget Android — PR #33

- [x] Conflits résolus et branche remise à jour avec `main` (2026-10-13, CI 4/4 verte) ; description de la PR actualisée ; revue Codex demandée.
- [x] Documentation du Mute corrigée : mute Cast confirmé par le récepteur, volume fixe, aucun effet audible (le vrai mute passe par le canal télécommande).
- [ ] Tester sur le téléphone, avec ton accord pour chaque commande : Lecture/Pause, Stop, Volume.
- [ ] Suite possible : faire passer le Mute du widget par le canal télécommande (le son réellement entendu).
- [ ] Fusion de la #33 : ta décision, après ces essais.

## 5. Phase 3 — livraison

- [ ] Installer le `.deb` et l'APK finaux sur le PC et le téléphone, puis refaire la recette complète.
- [ ] Documentation d'utilisation : appairage, clavier, limites (par exemple, cette TV ne rapporte pas son volume).
- [ ] Icône dans la barre système Ubuntu (élément K) : **codée** (branche `feat/linux-tray`) — Ouvrir, Lecture/Pause, Stop, Mute TV, Volume TV −/+, Quitter ; fermer la fenêtre quitte l'application (décision à confirmer). À voir sur ton bureau Ubuntu.

## Suivi

| Lot | Avancement | Restant estimé |
|---|---|---|
| Phase 1 : autonomie et convergence | tests simulés 100 %, fusionnés (#38) ; matériel partiel, non classé | 14–28 h (enveloppe) |
| Phase 2 : commandes, applications, clavier | son validé sur Ubuntu ; le reste codé, 0 % confirmé sur TV ; applications 0 % | 16–32 h, à réestimer après la décision ADB |
| Phase 3 : livraison | builds CI disponibles ; recette finale à faire | 10–20 h |
