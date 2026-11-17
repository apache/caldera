# Malbas plugin

Malomatia's breach-and-attack-simulation content for MITRE Caldera, packaged as
a data-only plugin so it loads automatically on every build of this fork.

## What's inside
- `data/adversaries/` — Qatar-sector APT emulation profiles and per-tactic test libraries
- `data/abilities/` — custom abilities backing the profiles (grouped by ATT&CK tactic)
- `data/payloads/` — payload files referenced by the abilities

## How it loads
`malbas` is listed under `plugins:` in `conf/default.yml`. Caldera's data
service loads everything under `data/` at startup — no code needed in `hook.py`.

## Updating the content
The source of truth for this content is the `raghadxxx/Mal-BAS` repository.
To refresh this plugin, copy its `adversaries/`, `abilities/` and `payloads/`
folders into `data/` here and commit.
