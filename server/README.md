# Server scripts

`EnrichItems.php` adds the Wikidata statements the SV infoboxes read to local Wikibase items that
lack them (WikibaseImport's own script skips items that already have any statement).

Install outside the docroot, next to the MediaWiki install, and run on the repo wiki (`en`).
`<vhost-user>`/`<group>` are the web server's system user and group, `<tools-dir>` the install
directory and `<mw-root>` the MediaWiki root (the script falls back to `../seminaverbi_1_44/w` from
its own directory when `MW_INSTALL_PATH` is unset):

```
sudo install -d -o <vhost-user> -g <group> <tools-dir>
sudo install -m 644 -o <vhost-user> -g <group> server/EnrichItems.php server/properties.txt <tools-dir>/
IP=<mw-root>
$IP/maintenance/run --wiki en <tools-dir>/EnrichItems.php --item Q4178 --dry-run
$IP/maintenance/run --wiki en <tools-dir>/EnrichItems.php --from-articles en,it --dry-run
```

Drop `--dry-run` to write. Check the size of the `seminaverbi_en` database before and after a full
run (the plan's guardrail: stop and report if it grows by more than 200 MB).

`properties.txt` lists Wikidata property IDs; each is mapped to the local property through
WikibaseImport's mapping table.
