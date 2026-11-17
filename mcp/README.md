# seminaverbi MCP server

Claude Code reads and edits the six SeminaVerbi wikis through
[`@professional-wiki/mediawiki-mcp-server`](https://www.npmjs.com/package/@professional-wiki/mediawiki-mcp-server)
(MIT), logged in as **SV editor bot** (bot password `mcp`). Its edits are bot-flagged and tagged
`seminaverbi-mcp`, so `Special:RecentChanges?tagfilter=seminaverbi-mcp` lists exactly what it did.
How the bot is expected to edit is in `skills/seminaverbi-editor/`.

- `config.json`: the six wikis (keys `en it es fr de pt`). No secrets: the username and password are
  read at startup from `~/.config/seminaverbi/editor.env` (`SV_EDITOR_USER`, `SV_EDITOR_PASSWORD`, mode
  600) by the inline `exec` commands.
- The server is installed at a pinned version in `~/.local/share/seminaverbi-mcp` and run with an
  absolute Node path (it needs Node ≥ 22.19):

```
N=~/.local/share/fnm/node-versions/v22.23.2/installation/bin
mkdir -p ~/.local/share/seminaverbi-mcp && cd ~/.local/share/seminaverbi-mcp
PATH=$N:$PATH $N/npm install --save-exact @professional-wiki/mediawiki-mcp-server@0.21.0
claude mcp add -s user seminaverbi -e CONFIG=<repo>/mcp/config.json -- \
  $N/node ~/.local/share/seminaverbi-mcp/node_modules/@professional-wiki/mediawiki-mcp-server/dist/index.js
```

**Upgrading** is a deliberate change: read the release notes (new write tools, config changes), install
the new exact version the same way, and run the smoke test (`whoami` shows `SV editor bot` in `bot` and
not `sysop`; an edit to `User:SV editor bot/sandbox` carries the tag; `delete-page` is refused).

**Revoking**: delete the bot password `mcp` at Special:BotPasswords (as the bot) or with the server's
maintenance tools, or `claude mcp remove seminaverbi -s user`.
