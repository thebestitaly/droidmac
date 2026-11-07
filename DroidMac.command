#!/bin/bash
# Doppio clic dal Finder per avviare DroidMac senza installare l'app.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 non trovato: installa gli strumenti da riga di comando con  xcode-select --install"
  read -r -p "Premi Invio per chiudere…"
  exit 1
fi
exec python3 -m droidmac "$@"
