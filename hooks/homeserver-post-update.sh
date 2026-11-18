#!/bin/bash
# Omarchy-Hook (post-update): stellt das Widget in der Leiste wieder her, falls ein
# Update oder 'omarchy refresh shell' das Layout zurueckgesetzt hat. Das Plugin selbst
# (~/.config/omarchy/plugins/homeserver) bleibt davon unberuehrt; nur der Eintrag in
# shell.json kann verloren gehen. Die Arbeit erledigt install.sh --bar-only.
link="$HOME/.config/omarchy/plugins/homeserver"
[[ -L $link ]] || exit 0
repo="$(dirname "$(readlink -f "$link")")"
[[ -x $repo/install.sh ]] && exec "$repo/install.sh" --bar-only
exit 0
