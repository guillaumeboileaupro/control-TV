# Diagnostic des commandes de l'application

L'hôte Tauri écrit un journal JSONL persistant et borné pour chaque appel au bridge Python. Cet
enregistrement se fait dans un thread dédié via une file bornée : une écriture lente ou impossible
ne bloque jamais la fenêtre et, si la file de 256 événements est pleine, seul le diagnostic est
abandonné.

Sous Ubuntu, le fichier est normalement :

```text
${XDG_DATA_HOME:-$HOME/.local/share}/io.github.guillaumeboileaupro.controltv/logs/commands.jsonl
```

`commands.jsonl.1` contient la rotation précédente. Chaque fichier est limité à 2 Mio, le
répertoire est privé (`0700`) et les journaux sont privés (`0600`). Pour récupérer les derniers
événements après un blocage, sans relancer une commande :

```bash
tail -n 200 "${XDG_DATA_HOME:-$HOME/.local/share}/io.github.guillaumeboileaupro.controltv/logs/commands.jsonl"
```

Chaque ligne contient seulement `timestamp_ms`, `correlation_id`, `phase`, `method` et, lorsqu'ils
existent, `duration_ms` et `error_code`. Les phases attendues sont :

- `command_received` : Tauri a reçu l'appel de la fenêtre ;
- `bridge_dispatched` : la requête a été confiée au bridge Python ;
- `command_completed`, `command_failed` ou `command_timed_out` : issue terminale.

Le même `correlation_id` devient le `requestId` Python et, lorsque la trace d'acceptation C6/C7 est
activée, son `application_request_id`. Le journal n'enregistre jamais les paramètres, messages
d'erreur libres, noms ou adresses d'appareils, textes saisis, codes d'appairage, jetons ou secrets.

Pour isoler une commande avec `jq` :

```bash
jq -c 'select(.correlation_id == "COLLER_IDENTIFIANT")' \
  "${XDG_DATA_HOME:-$HOME/.local/share}/io.github.guillaumeboileaupro.controltv/logs/commands.jsonl"
```

Pour préparer T-05, le nombre d'événements `bridge_dispatched` mesure les tentatives locales
remises au bridge. Il ne prouve ni la réception par la TV, ni l'effet physique : T-05 devra ajouter
un accusé distinct du récepteur pour établir ce second comptage.
