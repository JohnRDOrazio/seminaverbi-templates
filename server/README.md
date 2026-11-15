# Server scripts

`EnrichItems.php` adds the Wikidata statements the SV infoboxes read to local Wikibase items that
lack them (WikibaseImport's own script skips items that already have any statement).

Install outside the docroot and run on the repo wiki (`en`):

```
sudo install -d -o bibleget.io_q71zp2mh9vh -g psacln /var/www/vhosts/bibleget.io/seminaverbi-tools
sudo install -m 644 -o bibleget.io_q71zp2mh9vh -g psacln server/EnrichItems.php server/properties.txt /var/www/vhosts/bibleget.io/seminaverbi-tools/
IP=/var/www/vhosts/bibleget.io/seminaverbi_1_44/w
$IP/maintenance/run --wiki en /var/www/vhosts/bibleget.io/seminaverbi-tools/EnrichItems.php --item Q4178 --dry-run
$IP/maintenance/run --wiki en /var/www/vhosts/bibleget.io/seminaverbi-tools/EnrichItems.php --from-articles en,it --dry-run
```

Drop `--dry-run` to write. Check the size of the `seminaverbi_en` database before and after a full
run (the plan's guardrail: stop and report if it grows by more than 200 MB).

`properties.txt` lists Wikidata property IDs; each is mapped to the local property through
WikibaseImport's mapping table.
