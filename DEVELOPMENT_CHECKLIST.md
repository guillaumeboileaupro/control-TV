# control-TV — Checklists exhaustives de développement et de recette

> Référence opérationnelle de l'issue [#37](https://github.com/guillaumeboileaupro/control-TV/issues/37), complément de [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md). Priorité : **deux applications autonomes Ubuntu et Android, simultanément sur la même Google TV/Chromecast**, sans serveur permanent. **MCP et ChatGPT Voice reportés**. Ne jamais fusionner de PR ni envoyer de commande à la télévision réelle sans accord explicite du propriétaire.

## Mode d'emploi et preuves

- [ ] Avant chaque lot, définir les critères d'acceptation, responsable, dépendances et risques.
- [ ] Pour chaque ligne, noter son état **NON COMMENCÉ / EN COURS / BLOQUÉ / VALIDÉ SIMULATION / VALIDÉ MATÉRIEL / NON APPLICABLE justifié**.
- [ ] Cocher `[x]` uniquement après une preuve correspondant au libellé : une CI verte ne vaut pas une validation matérielle.
- [ ] Conserver la preuve : SHA, version Ubuntu/Android/TV, date, commandes de test, résultat, logs expurgés, capture si autorisée.
- [ ] Distinguer la fonctionnalité implémentée sur une plateforme et sa validation sur cette plateforme.
- [ ] Noter les limitations propres au modèle TV et à l'application, sans déclarer universel un comportement particulier.
- [ ] À chaque compte rendu, indiquer **avancement fonctionnel (%), estimation, temps passé par tâche s'il est connu, temps restant réévalué, budget consommé (%), preuve, blocage**. Le cumul historique d'heures n'est pas un indicateur de synthèse prioritaire.
- [ ] Réviser le budget restant après tout risque technique confirmé, sans double compter les sous-tâches déjà comprises dans les phases.
- [ ] Ne jamais enregistrer d'identifiant personnel, jeton, adresse IP privée ou contenu sensible en clair dans les rapports publics.
- [ ] Mettre à jour les checklists et le plan à chaque livraison ou découverte majeure.

### Référence de départ (revue Claude de PR #38, 2026-10-08)

- [x] Revue indépendante du commit `c55bc4d6b58a5f8d5964a84165bc2a0884aad1ac` : **APPROVED**, sans P0/P1/P2 ; sept points P3 facultatifs.
- [x] Contrôle de l'indépendance de deux `ControlService` et des 17 cas de convergence simulée.
- [x] Vérification des quatre corrections P2 de Codex.
- [x] Tests locaux rapportés : **1207 réussis, 8 ignorés**, couverture **98,02 %** ; CI de référence : **1206 réussis, 9 ignorés**, 4/4 jobs verts. Différence liée à l'environnement de tests.
- [x] Revue de reproductibilité du protocole R1–R4 : **prêt à exécuter avec autorisation**.
- [ ] Exécuter R1–R4 sur les deux plateformes réelles : **NON FAIT**.
- [ ] Fusionner PR #38 : **NON AUTORISÉ** (approbation technique ≠ autorisation du propriétaire).

## Phase 0 — Gouvernance, exigences et architecture (transverse)

**Objectif :** ne perdre aucune exigence utilisateur et garantir des frontières claires.

- [ ] Recenser toutes les fonctions de la télécommande Ubuntu : découverte, sélection TV, statut, lecture, navigation, volume, veille, applications installées, lancement, clavier distant.
- [ ] Recenser les mêmes fonctions pour la télécommande Android ; comparer avec Ubuntu dans une matrice de parité.
- [ ] Identifier les fonctions dépendantes de la marque/modèle/OS de la TV.
- [ ] Vérifier qu'aucune fonction essentielle n'exige un service cloud payant ou un serveur control-TV permanent.
- [ ] Documenter l'architecture : deux processus autonomes, `ControlService` partagé en code, instances et états séparés, TV comme source de vérité.
- [ ] Prévoir l'état hors ligne, perte de Wi-Fi, changement de réseau, changement d'adresse IP et redécouverte.
- [ ] Prévoir les permissions réseau locales et l'isolement client des points d'accès.
- [ ] Définir le comportement de l'UI en cas de commande confirmée, rejetée, non confirmée ou expirée.
- [ ] Maintenir PR #33 (widget Android) indépendante et PR #36 (MCP) suspendue.
- [ ] Documenter les autorisations du propriétaire avant commandes réelles et avant merge.
- [ ] Rattacher chaque exigence aux tests automatisés, à la recette matérielle et à la preuve de livraison.
- [ ] Réévaluer les budgets : **phase 1 14–28 h ; phase 2 16–32 h ; phase 3 10–20 h ; total provisoire 40–80 h**. Les exigences de clavier et de lancement d'apps peuvent augmenter ce total.

## Phase 1 — Deux applications autonomes et convergence (14–28 h provisoires)

### 1.1 Socle logiciel et indépendance

- [x] Disposer d'un moteur Python `ControlService` commun au code Ubuntu/Android.
- [x] Disposer de tests simulant deux services indépendants face à une même TV.
- [x] Vérifier en simulation l'absence d'état partagé de service au niveau module.
- [ ] Vérifier sur Ubuntu le démarrage sans Android ni serveur tiers.
- [ ] Vérifier sur Android le démarrage sans Ubuntu ni serveur tiers.
- [ ] Vérifier l'ouverture des deux applications simultanément.
- [ ] Vérifier qu'arrêter l'une ne ferme ni ne bloque l'autre.
- [ ] Vérifier que les deux contrôleurs sélectionnent **le même identifiant stable** de TV.
- [ ] Vérifier la redécouverte après redémarrage de la TV.
- [ ] Vérifier la redécouverte après coupure/reprise du Wi-Fi.
- [ ] Vérifier le cas de plusieurs Chromecast/TV visibles et le changement de cible.
- [ ] Vérifier l'absence de commande envoyée lors d'une simple lecture d'état.

### 1.2 PR #38 — Tests de convergence et revue

- [x] Tests automatisés : découverte indépendante.
- [x] Tests automatisés : convergence au prochain rafraîchissement.
- [x] Tests automatisés : chevauchement forcé des commandes.
- [x] Tests automatisés : changement de session média après envoi.
- [x] Tests automatisés : livraison ambiguë zéro ou une réception.
- [x] Tests automatisés : absence de rejeu automatique.
- [x] Revue indépendante Claude : **APPROVED**, aucun P0/P1/P2.
- [x] P3-1 : corriger `docs/REQUIREMENTS_TRACEABILITY.md` (16 → 17 tests), si retenu. Fait (branche PR #38, non fusionnée).
- [ ] P3-2 : actualiser la description de la PR #38 (17 tests ; livraison zéro-ou-une), si retenu.
- [x] P3-3 : ajouter un test de changement de session **entre prélecture et envoi**, si retenu. Fait (branche PR #38, non fusionnée) : lacune confirmée, la commande atteint la nouvelle session (jamais confirmée ni rejouée) ; figée par un test et un `xfail` strict ; correction hors PR #38.
- [x] P3-4 : reformuler R4 : absence de changement visible/audible, pas preuve réseau d'absence d'envoi. Fait (branche PR #38, non fusionnée).
- [x] P3-5 : clarifier R3 : aucune commande **depuis control-TV** ; action manuelle du propriétaire sur la TV attendue. Fait (branche PR #38, non fusionnée).
- [x] P3-6 : critères PASS/FAIL/NOT RUN par étape et code d'erreur de lecture. Fait (branche PR #38, non fusionnée).
- [x] P3-7 : vérifier les confirmations de commandes supplantées contre l'historique d'état lu par le simulateur. Fait (branche PR #38, non fusionnée).
- [ ] Après corrections facultatives retenues, exécuter les tests pertinents et vérifier les quatre jobs CI sur le nouveau HEAD.
- [ ] Demander l'autorisation explicite du propriétaire **avant toute fusion de PR #38**.

### 1.3 R1–R4 — Recette réelle en lecture seule

**Prérequis :** deux appareils opérationnels, même réseau local, builds/commits consignés, revue Claude terminée, autorisation du propriétaire, aucune commande de control-TV pendant ces tests.

- [ ] Vérifier que le protocole indique le modèle TV, OS/firmware, versions des applications et réseau.
- [ ] Vérifier que chaque test a des critères PASS, FAIL et NOT RUN/INCONCLUSIF et une méthode de preuve.
- [ ] **R1 Ubuntu** : découvrir la TV depuis Ubuntu et relever son identifiant stable.
- [ ] **R1 Android** : découvrir la TV depuis Android indépendamment et comparer l'identifiant.
- [ ] **R2** : lire l'état initial sur Ubuntu puis Android et comparer session, lecture, application et volume logique disponibles.
- [ ] **R3** : modifier manuellement la TV avec sa télécommande physique ou une application de diffusion externe ; observer les rafraîchissements **sans commande control-TV**.
- [ ] **R3** : vérifier que les deux applications convergent vers le nouvel état et noter délais/anomalies.
- [ ] **R4** : enchaîner des rafraîchissements sur Ubuntu et Android, noter erreurs, stabilité et changements visibles/audibles.
- [ ] Vérifier le cas d'une erreur de lecture : consigner code, heure et statut sans marquer PASS.
- [ ] Consigner PASS/FAIL/NOT RUN séparément pour R1, R2, R3, R4.
- [ ] Déclarer la phase matériellement validée **uniquement si tous les critères essentiels sont satisfaits**.

### 1.4 Fiabilisation après R1–R4

- [ ] Trier les anomalies par plateforme, reproductibilité, gravité et impact utilisateur.
- [ ] Corriger les erreurs de découverte, d'identité stable et de rafraîchissement.
- [ ] Tester les reconnexions après perte de réseau, sommeil de téléphone et sortie de veille PC.
- [ ] Vérifier les délais d'attente, messages d'erreur et récupération sans boucle infinie.
- [ ] Vérifier l'absence de verrou partagé inter-applications.
- [ ] Rejouer R1–R4 après correction, avec autorisation renouvelée si nécessaire.
- [ ] Obtenir un état stable des deux applications ouvertes simultanément.

**Sortie phase 1 :** Ubuntu et Android découvrent et suivent réellement la même TV, indépendamment et simultanément ; preuves consignées.

## Phase 2 — Fonctions de télécommande sur TV réelle (16–32 h avant réévaluation)

### 2.1 Lecture et média

- [ ] Afficher l'état lecture/pause/arrêt, média, application et session lorsque disponibles.
- [ ] Lecture depuis Ubuntu, puis observation du résultat sur les deux applications.
- [ ] Pause depuis Ubuntu, puis observation sur les deux applications.
- [ ] Reprise depuis Android, puis observation sur les deux applications.
- [ ] Stop depuis chaque plateforme si pris en charge.
- [ ] Vérifier les états sans média, changement de contenu, fin de lecture et changement de session.
- [ ] Gérer les refus, timeouts, états ambigus et absence de rejeu automatique.
- [ ] Documenter les commandes indisponibles selon le récepteur.

### 2.2 Navigation TV

- [ ] Identifier les protocoles réellement disponibles pour navigation Google TV (Cast seul potentiellement insuffisant).
- [ ] Flèches haut/bas/gauche/droite depuis Ubuntu et Android.
- [ ] OK/Select depuis Ubuntu et Android.
- [ ] Retour/Back depuis Ubuntu et Android.
- [ ] Accueil/Home depuis Ubuntu et Android.
- [ ] Vérifier répétition et maintien de touche, si pertinent et pris en charge.
- [ ] Vérifier les changements de focus et la latence.
- [ ] Vérifier l'effet dans l'accueil Google TV et au moins deux applications représentatives.
- [ ] Signaler les commandes non prises en charge, sans succès fictif.

### 2.3 Volume, Mute et volume **audible**

- [x] Preuve rapportée : Mute et Unmute confirmés au niveau receiver Cast.
- [x] Preuve rapportée : receiver `volume_control_type=fixed` ; **aucun changement sonore physique observé**.
- [ ] Afficher le niveau de volume logique et son type de contrôle (fixe/ajustable/inconnu).
- [ ] Tester Volume +/− depuis Ubuntu et Android sur récepteur ajustable, avec autorisation.
- [ ] Tester Mute/Unmute depuis les deux applications et relever confirmation Cast.
- [ ] Vérifier **séparément** l'effet réellement audible sur la TV.
- [ ] Vérifier le comportement d'un récepteur à volume fixe : ne pas promettre de variation audible.
- [ ] Étudier et tester une méthode alternative autorisée si le volume physique ne répond pas au Cast.
- [ ] Vérifier synchronisation du niveau et de Mute entre les deux interfaces.
- [ ] Éviter les commandes répétées après timeout ou livraison incertaine.

### 2.4 Alimentation TV

- [ ] Vérifier la faisabilité de veille et réveil sur le modèle précis de TV.
- [ ] Tester mise en veille depuis Ubuntu, puis depuis Android.
- [ ] Tester réveil depuis Ubuntu, puis depuis Android.
- [ ] Tester TV en veille profonde, si le matériel le permet.
- [ ] Distinguer état Chromecast, état écran et alimentation effective de la TV.
- [ ] Gérer TV hors réseau, Wake-on-LAN/CEC ou autre méthode seulement si prise en charge et autorisée.
- [ ] Afficher clairement une indisponibilité plutôt qu'une confirmation non prouvée.

### 2.5 **Applications installées sur la Google TV : découverte, liste et lancement**

**Exigence utilisateur obligatoire :** afficher les **applications réellement installées** sur la TV ciblée et les **lancer depuis Ubuntu et Android**. Un catalogue d'applications Cast connues n'est **pas** une liste des applications installées.

- [ ] Étudier comment interroger les applications réellement installées sur le modèle TV (Cast ne garantit pas cette capacité).
- [ ] Identifier les permissions, l'appairage, les contraintes de sécurité et la nécessité éventuelle d'un autre protocole.
- [ ] Documenter clairement les limitations de découverte exhaustive.
- [ ] Obtenir la liste des applications installées avec identifiant stable, nom et icône si accessibles.
- [ ] Distinguer application installée, détectée, lançable, non lançable et application Cast connue.
- [ ] Afficher la liste dans l'interface Ubuntu.
- [ ] Afficher la liste dans l'interface Android.
- [ ] Gérer liste vide, erreur de récupération, latence, doublons et applications système.
- [ ] Permettre recherche/filtre et sélection d'une application dans les deux interfaces.
- [ ] Lancer une application installée depuis Ubuntu et vérifier l'ouverture **sur la TV réelle**.
- [ ] Lancer une application installée depuis Android et vérifier l'ouverture **sur la TV réelle**.
- [ ] Tester plusieurs catégories d'applications (vidéo, musique, application système), selon celles réellement installées.
- [ ] Vérifier le cas d'une application installée mais non lançable via le protocole choisi.
- [ ] Vérifier que l'application active et son état remontent sur Ubuntu et Android après lancement.
- [ ] Actualiser la liste après installation d'une application sur la TV.
- [ ] Actualiser la liste après désinstallation d'une application sur la TV.
- [ ] Vérifier les erreurs « application absente », « accès refusé », « TV indisponible », « protocole non compatible ».
- [ ] Vérifier les lancements successifs et l'absence de rejeu automatique après résultat ambigu.
- [ ] Vérifier les deux contrôleurs ouverts simultanément pendant le lancement.
- [ ] Documenter les limites de compatibilité **sans présenter une liste partielle comme exhaustive**.

**Estimation initiale de la ligne historique : 3–5 h, insuffisamment étayée pour l'énumération et le lancement réels. Réestimer après étude technique.**

### 2.6 **Clavier distant : remplir un champ de texte sur la TV**

**Exigence utilisateur obligatoire :** saisir du texte dans les champs affichés sur la Google TV depuis le **clavier du PC Ubuntu** ou le **clavier du téléphone Android**.

- [ ] Étudier les protocoles de télécommande/saisie disponibles : Cast seul n'offre pas forcément une saisie universelle.
- [ ] Vérifier les prérequis d'appairage, autorisations et sécurité pour la TV concernée.
- [ ] Détecter, si techniquement possible, qu'un champ de texte TV est actif.
- [ ] Proposer dans Ubuntu un champ de saisie ou mode clavier distant ergonomique.
- [ ] Permettre la saisie via le clavier physique Ubuntu.
- [ ] Proposer dans Android un champ de saisie activant le clavier logiciel du téléphone.
- [ ] Envoyer des caractères simples et des espaces dans un champ TV compatible depuis Ubuntu.
- [ ] Envoyer des caractères simples et des espaces depuis Android.
- [ ] Tester les caractères accentués, apostrophes, chiffres, ponctuation et caractères spéciaux.
- [ ] Tester Backspace / suppression depuis les deux plateformes.
- [ ] Tester Entrée/OK / validation du champ depuis les deux plateformes.
- [ ] Tester déplacement du curseur et sélection si pris en charge ; sinon afficher la limite.
- [ ] Tester la recherche système Google TV.
- [ ] Tester la recherche dans une application vidéo compatible.
- [ ] Tester un autre champ applicatif compatible ; consigner les exceptions.
- [ ] Vérifier les champs protégés (mots de passe) : ne pas enregistrer ni exposer le texte saisi.
- [ ] Ne pas conserver de texte sensible dans les logs, analytics, historique ou presse-papiers sans nécessité explicite.
- [ ] Tester la saisie longue, les frappes rapides, les accents et la perte de focus.
- [ ] Vérifier le comportement si aucun champ n'est actif ou si l'application refuse la saisie.
- [ ] Vérifier la récupération après déconnexion/réappairage.
- [ ] Vérifier le fonctionnement pendant que l'autre télécommande est ouverte.
- [ ] Valider sur **TV réelle**, séparément depuis Ubuntu et Android, après autorisation.
- [ ] Documenter précisément les champs/applications non compatibles : **ne pas promettre une saisie universelle sans preuve**.

**Estimation : à établir après faisabilité. Nouvelle exigence à intégrer explicitement au budget phase 2 et au budget global.**

### 2.7 Commandes concurrentes C1–C7 et sûreté de livraison

- [ ] Définir la matrice C1–C7 : acteurs, état initial, commandes, ordre observé, attendu, preuve et verdict.
- [ ] Obtenir l'autorisation explicite du propriétaire pour **chaque campagne de commandes réelles**.
- [ ] Vérifier les commandes intercalées Ubuntu/Android sur la même TV.
- [ ] Vérifier l'ordre observé et le résultat sans supposer un ordre réseau garanti.
- [ ] Vérifier la convergence des états après les deux commandes.
- [ ] Vérifier qu'une livraison ambiguë ne déclenche aucun rejeu automatique.
- [ ] Vérifier les cas zéro ou une réception, selon les preuves réseau/récepteur disponibles.
- [ ] Pour C6, instrumenter le changement de session entre prélecture et confirmation.
- [ ] Pour C7, instrumenter tentatives côté émetteur et réceptions côté TV si observable.
- [ ] Séparer résultat du transport, état logique du receiver et effet visible/audible.
- [ ] Documenter les limites d'observation et les cas INCONCLUSIFS.

**Sortie phase 2 :** fonctions essentielles utilisables et validées sur TV réelle **depuis Ubuntu et Android**, y compris **lancement des applications installées** et **clavier distant** dans les limites techniques documentées. Toute exigence impossible sur le matériel doit être signalée au propriétaire, pas cochée artificiellement.

## Phase 3 — Qualité, interface, installation et livraison (10–20 h avant réévaluation)

### 3.1 Ergonomie et parité fonctionnelle

- [ ] Présenter clairement la TV sélectionnée et l'état de connexion sur chaque application.
- [ ] Afficher les états lecture/pause, application active, volume et erreurs sans ambiguïté.
- [ ] Prévoir les commandes essentielles accessibles en peu d'interactions.
- [ ] Afficher une liste d'applications TV lisible avec recherche et icônes si disponibles.
- [ ] Prévoir une expérience clavier distant confortable sur Ubuntu.
- [ ] Prévoir une expérience clavier distant confortable sur Android.
- [ ] Gérer les écrans petits, grands, rotation Android, redimensionnement Ubuntu.
- [ ] Prévoir états chargement, vide, non disponible, erreur, reconnexion et succès non confirmé.
- [ ] Vérifier les libellés et les limites de fonctions dépendantes du modèle TV.
- [ ] Vérifier l'accessibilité : tailles de cibles tactiles, navigation clavier, contraste et retour d'état.

### 3.2 Widget Android — PR #33 (indépendante)

- [x] Analyse Codex effectuée : architecture compatible par inspection ; PR actuellement conflictuelle.
- [x] Installation, placement, Refresh, picker et redessin rapportés comme testés sur téléphone.
- [ ] Résoudre conflits de `DEVELOPMENT_PLAN.md` et `README.md`, relire `ARCHITECTURE.md`.
- [ ] Conserver la priorité Ubuntu/Android et le report MCP/Voice.
- [ ] Corriger la documentation Mute : receiver-confirmed, volume fixe, effet sonore non prouvé.
- [ ] Relancer les quatre jobs CI sur le HEAD après résolution.
- [ ] Vérifier que le widget partage bien le `ControlService` de l'app Android sans nouveau moteur.
- [ ] Tester le widget après destruction/recréation du processus Android.
- [ ] Tester restrictions batterie, WorkManager, actualisation et mise à jour APK.
- [ ] Tester Play/Pause, Stop, Mute, Volume sur TV réelle **avec autorisation**.
- [ ] Vérifier la convergence entre widget, application Android et application Ubuntu.
- [ ] Vérifier la description PR et les limitations documentées.
- [ ] Obtenir autorisation explicite avant fusion PR #33.

### 3.3 Tests automatisés et non-régression

- [ ] Exécuter tests Python, typage, lint, couverture et vérifier les seuils existants.
- [ ] Exécuter tests Rust, frontend Tauri et intégration UI.
- [ ] Exécuter tests JVM/Kotlin Android.
- [ ] Vérifier CI complète après chaque correction affectant la livraison.
- [ ] Ajouter tests déterministes pour chaque régression constatée sur matériel.
- [ ] Tester erreurs réseau, timeouts, commandes rejetées et livraisons ambiguës.
- [ ] Tester les flux de découverte et sélection multi-TV.
- [ ] Ajouter tests de liste/lancement des applications installées avec capacités simulées et refus.
- [ ] Ajouter tests de saisie distante, caractères spéciaux, champs non compatibles et erreurs.
- [ ] Envisager tests Android instrumentés AppWidget/WorkManager et couverture Kotlin.
- [ ] Vérifier l'absence de secrets et données personnelles dans les journaux et artefacts.

### 3.4 Recette matérielle finale

- [ ] Installer les deux builds sur les appareils cibles.
- [ ] Ouvrir Ubuntu et Android simultanément et découvrir la même TV.
- [ ] Rejouer R1–R4, consigner PASS/FAIL/NOT RUN.
- [ ] Tester toutes les commandes de navigation et lecture prises en charge.
- [ ] Tester volume logique **et** effet sonore physique.
- [ ] Tester veille/réveil selon capacités du modèle.
- [ ] Tester **liste réelle des applications installées**, lancement depuis Ubuntu et Android.
- [ ] Tester **saisie clavier distante** depuis Ubuntu et Android.
- [ ] Tester commandes concurrentes C1–C7 autorisées.
- [ ] Tester changement de réseau, veille/réveil téléphone, redémarrage TV.
- [ ] Vérifier absence de serveur permanent, de dépendance entre les applications et de rejeu automatique.
- [ ] Recenser les limitations non résolues et obtenir une décision explicite sur chacune.

### 3.5 Packaging et documentation

- [ ] Générer le paquet Linux `.deb` sur CI et vérifier son intégrité.
- [ ] Installer le `.deb` sur Ubuntu cible ; tester lancement, icône, dépendances et désinstallation.
- [ ] Générer l'`.apk` Android, vérifier signature/version et intégrité.
- [ ] Installer l'`.apk` sur téléphone cible ; tester permissions, démarrage et mise à jour.
- [ ] Vérifier que les fonctionnalités essentielles fonctionnent **depuis les paquets installés**, pas seulement en mode développement.
- [ ] Fournir guide de première connexion/appairage, sélection de TV et résolution des erreurs.
- [ ] Documenter le lancement d'applications TV, le clavier distant, les protocoles et limites de compatibilité.
- [ ] Documenter volume logique vs audible, veille/réveil et sécurité réseau.
- [ ] Fournir matrice de compatibilité Ubuntu/Android/TV et résultats de recette.
- [ ] Mettre à jour README, ARCHITECTURE, DEVELOPMENT_PLAN et traçabilité des exigences.
- [ ] Fournir versions, SHA, changelog, liens des artefacts et procédures de rollback.
- [ ] Vérifier qu'aucun élément MCP/Voice reporté n'est présenté comme nécessaire au fonctionnement.
- [ ] Demander validation finale du propriétaire avant publication/fusion.

**Sortie phase 3 :** deux applications installables et utilisables au quotidien, avec preuves de recette réelle et limites connues.

## Critères de livraison bloquants — décision propriétaire

- [ ] Ubuntu fonctionne **seul** sans Android.
- [ ] Android fonctionne **seul** sans Ubuntu.
- [ ] Ubuntu et Android fonctionnent **ensemble** sur la même TV.
- [ ] Découverte, sélection et statut convergent sur matériel réel.
- [ ] Les commandes prises en charge ont été validées sur matériel réel.
- [ ] Les applications installées sont détectées et affichées **dans les deux interfaces**, selon les capacités confirmées.
- [ ] Une application installée peut être lancée **depuis Ubuntu** sur TV réelle.
- [ ] Une application installée peut être lancée **depuis Android** sur TV réelle.
- [ ] Un champ de texte compatible peut être rempli **depuis Ubuntu** sur TV réelle.
- [ ] Un champ de texte compatible peut être rempli **depuis Android** sur TV réelle.
- [ ] Le volume audible et la veille/réveil sont testés et les limites sont documentées.
- [ ] Aucune commande ambiguë n'est rejouée automatiquement.
- [ ] Paquets `.deb` et `.apk` installés et testés sur appareils cibles.
- [ ] CI verte sur les commits effectivement livrés.
- [ ] Aucun serveur permanent ni MCP/Voice requis.
- [ ] Le propriétaire a approuvé les éventuelles limitations et autorisé les merges/publications.

## Prochaine séquence concrète

1. **PR #38** : décider des corrections P3 documentaires (P3-1/2 ; clarifications R1–R4 P3-4/5/6), puis demander l'autorisation de fusion. **APPROVED techniquement ≠ fusion autorisée**.
2. **R1–R4** : obtenir autorisation pour la recette réelle en lecture seule, consigner les preuves et les anomalies.
3. **PR #33** : résoudre les conflits documentaires sans écraser le plan ; relancer CI ; valider le widget sur matériel avant fusion autorisée.
4. **Étude de faisabilité prioritaire phase 2** : déterminer sur la TV cible comment **énumérer/lancer les applications installées** et **saisir du texte** depuis les deux plateformes ; mettre à jour les estimations.
5. **Commandes et livraison** : tester les scénarios autorisés, fiabiliser et produire les deux paquets.

**Rappel :** la revue Claude est un rapport fourni par le propriétaire ; les éléments cochés ci-dessus en reprennent les constats, sans prétendre à une vérification matérielle indépendante supplémentaire.
