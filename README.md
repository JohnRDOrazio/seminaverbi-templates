# SeminaVerbi shared templates

The `SV …` templates and `Module:SV/…` Lua modules used on every SeminaVerbi wiki
(`en it es fr de pt`.seminaverbi.bibleget.io). **This repository is the source of truth:** the
pages on the wikis are overwritten by the sync script, so edit them here and open a pull request.

- `modules/SV/*.lua` → `Module:SV/*`
- `templates/<name>.wikitext` → `Template:<name>` (and `templates/SV infobox/styles.css` → TemplateStyles)
- `config/*.json` and `i18n/*.json` → compiled into `Module:SV/Data`
- `server/` → maintenance script that fills Wikibase items from Wikidata (runs on the server)

Design: `docs/superpowers/specs/2026-11-14-shared-templates-design.md`.

## Test

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
```

Lua runs under Lua 5.1 (Scribunto's dialect) through `lupa`, with stubs for `mw`,
`mw.wikibase` and `mw.language` in `tests/lua_env.py`.

## License

Copyright (C) 2026 John R. D'Orazio and contributors.

This program is free software; you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation; either version 2 of the License,
or (at your option) any later version. See [LICENSE](LICENSE).
