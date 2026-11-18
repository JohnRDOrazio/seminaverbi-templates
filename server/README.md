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
run, targets that are not mapped yet are not listed; on a real run a target that cannot be created
(`! creating <wd> failed`) makes the run end with an error listing it, so it is retried.

`--dry-run` with `--import` writes nothing: an ID that is already mapped prints `(mapped)`; one that is
not prints `(would create)`, and, as nothing is created, a not-yet-mapped item is not enriched either
(it has no local ID), so the later enrichment shows only the already-mapped imports. Every `--import`,
`--item` and `--follow` ID is validated first, dry run or not: any invalid one (case is normalised, so
`q5` is `Q5`) stops the run before anything is written, after printing `IMPORT <id> FAILED invalid ID`
(the echoed text is sanitised and capped at 40 characters).

`properties.txt` lists Wikidata property IDs; each is mapped to the local property through
WikibaseImport's mapping table.

## Connecting a new article (import first, then link)

An article is connected to its item before it is written. Search the local Wikibase (the MCP tool
`wikibase-search-entities`; local IDs differ from Wikidata IDs). If the subject is missing, import it
with `--import Q…` as above, then link the article with `python -m tools.connect_items`: `--wd TITLE=QID`
for a Wikidata item (pass the import output with `--import-log FILE`), or `--create "TITLE=description"`
when Wikidata has none. It reports only until `--apply` is given.
