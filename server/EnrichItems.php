<?php
/**
 * Fills SeminaVerbi's local Wikibase items with the Wikidata statements the SV infoboxes read.
 *
 * Local items were imported with WikibaseImport, whose mapping table (wbs_entity_mapping) links
 * each local ID to its Wikidata ID. Its own importEntities.php skips items that already have any
 * statement and no longer runs on MediaWiki 1.44, so this script is self-contained: for each item
 * it adds every allow-listed property the item does not have yet, creating referenced items as
 * needed (labels and descriptions in the SeminaVerbi languages, sitelinks to those Wikipedias),
 * and copies main values only, without qualifiers or references.
 *
 *   maintenance/run --wiki en /var/www/vhosts/bibleget.io/seminaverbi-tools/EnrichItems.php \
 *       [--item Q4178]... [--from-articles en,it] [--properties FILE] [--dry-run]
 */

use MediaWiki\MediaWikiServices;
use MediaWiki\User\User;
use Wikibase\DataModel\Entity\EntityId;
use Wikibase\DataModel\Entity\EntityIdValue;
use Wikibase\DataModel\Entity\Item;
use Wikibase\DataModel\Entity\ItemId;
use Wikibase\DataModel\Entity\NumericPropertyId;
use Wikibase\DataModel\SiteLink;
use Wikibase\DataModel\Snak\PropertyValueSnak;
use Wikibase\DataModel\Statement\Statement;
use Wikibase\Lib\Store\LookupConstants;
use Wikibase\Repo\WikibaseRepo;

$IP = getenv( 'MW_INSTALL_PATH' ) ?: '/var/www/vhosts/bibleget.io/seminaverbi_1_44/w';
require_once "$IP/maintenance/Maintenance.php";

class EnrichItems extends Maintenance {

	private const LANGS = [ 'en', 'it', 'es', 'fr', 'de', 'pt', 'la' ];
	private const WIKIS = [ 'enwiki', 'itwiki', 'eswiki', 'frwiki', 'dewiki', 'ptwiki' ];
	private const SOURCE = 'https://www.wikidata.org/w/api.php';

	private bool $dryRun = false;
	private ?User $user = null;
	/** @var array<string, Item|null> */
	private array $remoteCache = [];

	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Add missing Wikidata statements to local items used by SV infoboxes.' );
		$this->addOption( 'item', 'Local item ID (repeatable)', false, true, false, true );
		$this->addOption( 'from-articles', 'Comma-separated wiki IDs whose main-namespace articles give the items', false, true );
		$this->addOption( 'properties', 'File of Wikidata property IDs to copy', false, true );
		$this->addOption( 'dry-run', 'Print what would be added; write nothing' );
	}

	public function execute() {
		$this->dryRun = $this->hasOption( 'dry-run' );
		$this->user = User::newSystemUser( 'SV enrichment', [ 'steal' => true ] );
		$lookup = WikibaseRepo::getEntityRevisionLookup();
		$store = WikibaseRepo::getEntityStore();
		$file = $this->getOption( 'properties', __DIR__ . '/properties.txt' );
		$wdProps = array_values( array_filter( array_map( 'trim', file( $file ) ) ) );

		foreach ( $this->localItems() as $localSerialization ) {
			$localId = new ItemId( $localSerialization );
			$wdSerialization = $this->originalId( $localSerialization );
			if ( !$wdSerialization ) {
				$this->output( "$localSerialization: no Wikidata mapping, skipped\n" );
				continue;
			}
			$revision = $lookup->getEntityRevision( $localId, 0, LookupConstants::LATEST_FROM_MASTER );
			$remoteItem = $this->remote( $wdSerialization );
			if ( !$revision || !$remoteItem ) {
				$this->output( "$localSerialization: could not load local or Wikidata item, skipped\n" );
				continue;
			}
			/** @var Item $item */
			$item = $revision->getEntity();
			$added = [];
			foreach ( $wdProps as $wdProp ) {
				$localPropSerialization = $this->localId( $wdProp );
				if ( !$localPropSerialization ) {
					continue;
				}
				$localProp = new NumericPropertyId( $localPropSerialization );
				if ( !$item->getStatements()->getByPropertyId( $localProp )->isEmpty() ) {
					continue;
				}
				$statements = $remoteItem->getStatements()->getByPropertyId( new NumericPropertyId( $wdProp ) );
				foreach ( $statements->toArray() as $statement ) {
					$snak = $statement->getMainSnak();
					if ( !$snak instanceof PropertyValueSnak || $statement->getRank() === Statement::RANK_DEPRECATED ) {
						continue;
					}
					$value = $snak->getDataValue();
					$shown = $value instanceof EntityIdValue
						? $value->getEntityId()->getSerialization()
						: json_encode( $value->getArrayValue(), JSON_UNESCAPED_UNICODE );
					if ( $value instanceof EntityIdValue ) {
						$localRef = $this->ensureLocal( $value->getEntityId() );
						if ( $localRef === null && !$this->dryRun ) {
							$this->output( "  ! $shown could not be created locally, value skipped\n" );
							continue;
						}
						if ( $localRef !== null ) {
							$value = new EntityIdValue( new ItemId( $localRef ) );
						}
					}
					$copy = new Statement( new PropertyValueSnak( $localProp, $value ) );
					$copy->setRank( $statement->getRank() );
					$copy->setGuid( ( new \Wikibase\DataModel\Services\Statement\GuidGenerator() )->newGuid( $localId ) );
					$item->getStatements()->addStatement( $copy );
					$added[] = "$localPropSerialization ($wdProp) = $shown";
				}
			}
			if ( !$added ) {
				$this->output( "$localSerialization (wd $wdSerialization): nothing to add\n" );
				continue;
			}
			$this->output( "$localSerialization (wd $wdSerialization):\n  + " . implode( "\n  + ", $added ) . "\n" );
			if ( !$this->dryRun ) {
				$store->saveEntity( $item, "Enrich from Wikidata $wdSerialization", $this->user, EDIT_UPDATE );
			}
		}
		$this->output( $this->dryRun ? "Dry run: nothing written.\n" : "Done.\n" );
	}

	/** Local ID for a Wikidata item, creating a lean copy if it is not mapped yet. */
	private function ensureLocal( EntityId $wdId ): ?string {
		$wd = $wdId->getSerialization();
		$local = $this->localId( $wd );
		if ( $local || $this->dryRun || !$wdId instanceof ItemId ) {
			return $local;
		}
		$remote = $this->remote( $wd );
		if ( !$remote ) {
			return null;
		}
		$item = new Item();
		foreach ( self::LANGS as $lang ) {
			if ( $remote->getLabels()->hasTermForLanguage( $lang ) ) {
				$item->setLabel( $lang, $remote->getLabels()->getByLanguage( $lang )->getText() );
			}
			if ( $remote->getDescriptions()->hasTermForLanguage( $lang ) ) {
				$item->setDescription( $lang, $remote->getDescriptions()->getByLanguage( $lang )->getText() );
			}
		}
		foreach ( self::WIKIS as $site ) {
			if ( $remote->getSiteLinkList()->hasLinkWithSiteId( $site ) ) {
				$title = $remote->getSiteLinkList()->getBySiteId( $site )->getPageName();
				$item->getSiteLinkList()->addSiteLink( new SiteLink( $site, $title ) );
			}
		}
		try {
			$saved = WikibaseRepo::getEntityStore()->saveEntity( $item, "Import $wd from Wikidata", $this->user, EDIT_NEW );
		} catch ( Exception $e ) {
			$this->output( "  ! creating $wd failed: " . $e->getMessage() . "\n" );
			return null;
		}
		$local = $saved->getEntity()->getId()->getSerialization();
		$this->dbw()->newInsertQueryBuilder()
			->insertInto( 'wbs_entity_mapping' )
			->row( [ 'wbs_local_id' => $local, 'wbs_original_id' => $wd ] )
			->caller( __METHOD__ )
			->execute();
		$this->output( "  created $local for $wd\n" );
		return $local;
	}

	private function remote( string $wd ): ?Item {
		if ( !array_key_exists( $wd, $this->remoteCache ) ) {
			$url = wfAppendQuery( self::SOURCE, [
				'action' => 'wbgetentities', 'ids' => $wd, 'format' => 'json',
				'props' => 'labels|descriptions|claims|sitelinks',
			] );
			$json = MediaWikiServices::getInstance()->getHttpRequestFactory()->get( $url, [], __METHOD__ );
			$data = $json ? json_decode( $json, true ) : null;
			$serialization = $data['entities'][$wd] ?? null;
			$this->remoteCache[$wd] = null;
			if ( $serialization && !isset( $serialization['missing'] ) ) {
				$entity = WikibaseRepo::getBaseDataModelDeserializerFactory()
					->newEntityDeserializer()->deserialize( $serialization );
				$this->remoteCache[$wd] = $entity instanceof Item ? $entity : null;
			}
		}
		return $this->remoteCache[$wd];
	}

	private function localId( string $wd ): ?string {
		$v = $this->dbw()->newSelectQueryBuilder()->select( 'wbs_local_id' )->from( 'wbs_entity_mapping' )
			->where( [ 'wbs_original_id' => $wd ] )->caller( __METHOD__ )->fetchField();
		return $v === false ? null : (string)$v;
	}

	private function originalId( string $local ): ?string {
		$v = $this->dbw()->newSelectQueryBuilder()->select( 'wbs_original_id' )->from( 'wbs_entity_mapping' )
			->where( [ 'wbs_local_id' => $local ] )->caller( __METHOD__ )->fetchField();
		return $v === false ? null : (string)$v;
	}

	private function dbw() {
		return MediaWikiServices::getInstance()->getConnectionProvider()->getPrimaryDatabase();
	}

	/** @return string[] local item IDs */
	private function localItems(): array {
		$items = (array)$this->getOption( 'item', [] );
		$wikis = $this->getOption( 'from-articles' );
		if ( $wikis ) {
			$provider = MediaWikiServices::getInstance()->getConnectionProvider();
			foreach ( explode( ',', $wikis ) as $wiki ) {
				$dbr = $provider->getReplicaDatabase( 'seminaverbi_' . trim( $wiki ) );
				$rows = $dbr->newSelectQueryBuilder()
					->select( 'pp_value' )
					->from( 'page_props' )
					->join( 'page', null, 'page_id = pp_page' )
					->where( [ 'pp_propname' => 'wikibase_item', 'page_namespace' => 0, 'page_is_redirect' => 0 ] )
					->caller( __METHOD__ )
					->fetchFieldValues();
				$items = array_merge( $items, $rows );
			}
		}
		return array_values( array_unique( $items ) );
	}
}

$maintClass = EnrichItems::class;
require_once RUN_MAINTENANCE_IF_MAIN;
