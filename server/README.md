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
export MW_INSTALL_PATH=<mw-root>
$MW_INSTALL_PATH/maintenance/run --wiki en <tools-dir>/EnrichItems.php --item Q4178 --dry-run
$MW_INSTALL_PATH/maintenance/run --wiki en <tools-dir>/EnrichItems.php --from-articles en,it --dry-run
```

Drop `--dry-run` to write. Check the size of the `seminaverbi_en` database before and after a full
run (the plan's guardrail: stop and report if it grows by more than 200 MB).

To import Wikidata items or properties that have no local counterpart yet, use `--import`
(repeatable; items are then enriched like `--item`), and `--follow` to enrich the items the selected
items point to through given Wikidata properties (for example the occupations and countries):

```
$MW_INSTALL_PATH/maintenance/run --wiki en <tools-dir>/EnrichItems.php \
    --import P21 --import P27 --import P2521 --import P1549 --import Q6581097 --import Q6581072 --dry-run
$MW_INSTALL_PATH/maintenance/run --wiki en <tools-dir>/EnrichItems.php --from-articles en,it --follow P106,P27 --dry-run
```

Record the `seminaverbi_en` size first; drop `--dry-run` to write (expect six `IMPORT <wd> = <local>`
lines and growth under 1 MB). Output lines: `IMPORT <wd> = <local> (mapped|created)`,
`IMPORT <wd> = (would create)` on a dry run, `IMPORT <wd> FAILED <reason>`. With `--follow` on a dry
run, targets that are not mapped yet are not listed.

`properties.txt` lists Wikidata property IDs; each is mapped to the local property through
WikibaseImport's mapping table.
