# control-TV — TODO

État au 2026-10-12. Liste courte des prochaines actions, dans l'ordre. La référence complète reste [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) (section « Plan complet et suivi du temps ») et [DEVELOPMENT_CHECKLIST.md](DEVELOPMENT_CHECKLIST.md).

**Objectif :** deux télécommandes autonomes, Ubuntu et Android, qui contrôlent la même Google TV en parallèle, sans serveur permanent. MCP, ChatGPT Voice et Windows sont abandonnés depuis le 2026-10-12.

**Règles :** aucune fusion et aucune commande envoyée à la TV sans l'accord explicite du propriétaire. Une CI verte n'est pas une validation sur matériel.

## 1. Fusions à décider (propriétaire)

- [ ] **#41** : le canal télécommande est intégré au `.deb` et à l'APK. CI verte.
- [ ] **#42** : clavier distant, Backspace/Entrée, application active, synchronisation du plan. À fusionner après #41.
- [ ] **#43** : retrait de MCP, ChatGPT Voice et Windows du plan, et ce fichier. À fusionner après #42.
- [ ] **#38** : tests de convergence Ubuntu/Android. Décider d'abord des corrections de documentation proposées par la revue (P3-1/P3-2 recommandées, ≈ 7 min).
- [ ] Code MCP encore présent dans `main` (PR #31) : le garder tel quel, ou le supprimer dans une PR séparée ?

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
| Applications installées : liste et lancement | ❌ 0 % | ❌ 0 % | **décision ADB** (ci-dessous) |
| Lecture, pause, stop, seek | partiellement observé | 0 % | essais C1–C7 autorisés |

- [ ] **Téléphone :** le brancher en USB, accepter le débogage, et autoriser l'installation de l'APK de la CI. Si les signatures diffèrent, le widget actuel sera perdu.
- [ ] **Décision ADB (applications installées, 2E) :** le débogage réseau de la Google TV est la seule méthode fiable trouvée pour lister les applications installées. Il s'active dans les options développeur de la TV et donne un accès complet à la TV depuis le réseau local. Sans ADB, l'alternative est un catalogue d'applications connues, présenté comme tel, jamais comme « installées ».
- [ ] Commandes concurrentes C1–C7 depuis Ubuntu et Android, avec autorisation.

## 4. Widget Android — PR #33

- [ ] Résoudre les conflits de documentation sans écraser le plan actuel.
- [ ] Relancer la CI, puis tester le widget sur la TV avec autorisation.

## 5. Phase 3 — livraison

- [ ] Installer le `.deb` et l'APK finaux sur le PC et le téléphone, puis refaire la recette complète.
- [ ] Documentation d'utilisation : appairage, clavier, limites (par exemple, cette TV ne rapporte pas son volume).
- [ ] Icône dans la barre système Ubuntu (élément K), après le widget.

## Suivi

| Lot | Avancement | Restant estimé |
|---|---|---|
| Phase 1 : autonomie et convergence | tests simulés 100 % ; matériel partiel, non classé | 14–28 h (enveloppe) |
| Phase 2 : commandes, applications, clavier | son validé sur Ubuntu ; le reste codé, 0 % confirmé sur TV ; applications 0 % | 16–32 h, à réestimer après la décision ADB |
| Phase 3 : livraison | builds CI disponibles ; recette finale à faire | 10–20 h |
