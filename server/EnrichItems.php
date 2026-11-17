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
 *   maintenance/run --wiki en <tools-dir>/EnrichItems.php \
 *       [--item Q4178]... [--from-articles en,it] [--properties FILE] [--dry-run]
 *       [--import Q123|P21]... [--follow P106,P27]
 *
 * --import creates a lean local copy of each given Wikidata item or property that is not mapped
 * yet (properties: datatype, labels, descriptions) and then enriches the imported items like
 * --item. --follow also enriches the items the selected items point to through the given Wikidata
 * properties (creating them if needed). Each --import prints one parseable line:
 *   IMPORT <wd> = <local> (mapped|created) | IMPORT <wd> = (would create) | IMPORT <wd> FAILED <reason>
 */

use MediaWiki\MediaWikiServices;
use MediaWiki\User\User;
use Wikibase\DataModel\Entity\EntityId;
use Wikibase\DataModel\Entity\EntityIdValue;
use Wikibase\DataModel\Entity\Item;
use Wikibase\DataModel\Entity\ItemId;
use Wikibase\DataModel\Entity\NumericPropertyId;
use Wikibase\DataModel\Entity\Property;
use Wikibase\DataModel\SiteLink;
use Wikibase\DataModel\Snak\PropertyValueSnak;
use Wikibase\DataModel\Statement\Statement;
use Wikibase\Lib\Store\LookupConstants;
use Wikibase\Repo\WikibaseRepo;

$IP = getenv( 'MW_INSTALL_PATH' ) ?: dirname( __DIR__ ) . '/seminaverbi_1_44/w';
require_once "$IP/maintenance/Maintenance.php";

class EnrichItems extends Maintenance {

	private const LANGS = [ 'en', 'it', 'es', 'fr', 'de', 'pt', 'la' ];
	private const WIKIS = [ 'enwiki', 'itwiki', 'eswiki', 'frwiki', 'dewiki', 'ptwiki' ];
	private const SOURCE = 'https://www.wikidata.org/w/api.php';

	private bool $dryRun = false;
	/** Null on a dry run, which must not create or take over the account. */
	private ?User $user = null;
	/** @var array<string, Item|Property|null> */
	private array $remoteCache = [];
	/** Reason the last importEntity() call returned null. */
	private string $lastError = '';

	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Add missing Wikidata statements to local items used by SV infoboxes.' );
		$this->addOption( 'item', 'Local item ID (repeatable)', false, true, false, true );
		$this->addOption( 'from-articles', 'Comma-separated wiki IDs whose main-namespace articles give the items', false, true );
		$this->addOption( 'properties', 'File of Wikidata property IDs to copy', false, true );
		$this->addOption( 'import', 'Wikidata item or property ID to import if not mapped yet (repeatable)', false, true, false, true );
		$this->addOption( 'follow', 'Comma-separated Wikidata property IDs whose item targets are enriched too', false, true );
		$this->addOption( 'dry-run', 'Print what would be added; write nothing' );
	}

	public function execute() {
		$this->dryRun = $this->hasOption( 'dry-run' );
		if ( !$this->dryRun ) {
			$this->user = User::newSystemUser( 'SV enrichment', [ 'steal' => true ] );
		}
		$lookup = WikibaseRepo::getEntityRevisionLookup();
		$store = WikibaseRepo::getEntityStore();
		$file = $this->getOption( 'properties', __DIR__ . '/properties.txt' );
		$wdProps = array_values( array_filter( array_map( 'trim', file( $file ) ) ) );

		/** @var array<string, true> items left incomplete or unsaved, reported once all items have run */
		$failed = [];
		$imported = $this->runImports( $failed );
		foreach ( $this->localItems( $imported ) as $localSerialization ) {
			$localId = new ItemId( $localSerialization );
			$wdSerialization = $this->originalId( $localSerialization );
			if ( !$wdSerialization ) {
				$this->output( "$localSerialization: no Wikidata mapping, skipped\n" );
				continue;
			}
			$revision = $lookup->getEntityRevision( $localId, 0, LookupConstants::LATEST_FROM_MASTER );
			$remoteItem = $this->remote( $wdSerialization );
			if ( !$revision || !$remoteItem instanceof Item ) {
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
				// Stage the property's statements; add none if any referenced item cannot be created, so the
				// property stays empty and a later run retries it (a partly filled property is skipped).
				$staged = [];
				$stagedShown = [];
				$complete = true;
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
							$this->output( "  ! $shown could not be created locally, $localPropSerialization ($wdProp) left for a later run\n" );
							$complete = false;
							$failed[$localSerialization] = true;
							break;
						}
						if ( $localRef !== null ) {
							$value = new EntityIdValue( new ItemId( $localRef ) );
						}
					}
					$copy = new Statement( new PropertyValueSnak( $localProp, $value ) );
					$copy->setRank( $statement->getRank() );
					$copy->setGuid( ( new \Wikibase\DataModel\Services\Statement\GuidGenerator() )->newGuid( $localId ) );
					$staged[] = $copy;
					$stagedShown[] = "$localPropSerialization ($wdProp) = $shown";
				}
				if ( !$complete ) {
					continue;
				}
				foreach ( $staged as $copy ) {
					$item->getStatements()->addStatement( $copy );
				}
				array_push( $added, ...$stagedShown );
			}
			if ( !$added ) {
				$this->output( "$localSerialization (wd $wdSerialization): nothing to add\n" );
				continue;
			}
			$this->output( "$localSerialization (wd $wdSerialization):\n  + " . implode( "\n  + ", $added ) . "\n" );
			if ( !$this->dryRun ) {
				try {
					// The base revision makes Wikibase refuse the save if someone edited the item meanwhile,
					// instead of overwriting their edit with this script's older copy.
					$store->saveEntity( $item, "Enrich from Wikidata $wdSerialization", $this->user, EDIT_UPDATE,
						$revision->getRevisionId() );
				} catch ( Exception $e ) {
					$failed[$localSerialization] = true;
					$this->output( "  ! saving $localSerialization failed (rerun to retry): " . $e->getMessage() . "\n" );
				}
			}
		}
		if ( $failed ) {
			$this->fatalError( count( $failed ) . ' item(s) incomplete or not saved (rerun to retry): '
				. implode( ', ', array_keys( $failed ) ) );
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
		$local = $this->importEntity( $wdId );
		if ( $local === null ) {
			$this->output( "  ! creating $wd failed: {$this->lastError}\n" );
			return null;
		}
		$this->output( "  created $local for $wd\n" );
		return $local;
	}

	/**
	 * Creates a lean local copy of a Wikidata item or property and maps it; returns the existing
	 * local ID if already mapped. Null on failure (reason in $lastError) and on a dry run.
	 */
	private function importEntity( EntityId $wdId ): ?string {
		$wd = $wdId->getSerialization();
		$local = $this->localId( $wd );
		if ( $local ) {
			return $local;
		}
		if ( $this->dryRun ) {
			$this->lastError = 'dry run';
			return null;
		}
		if ( !$wdId instanceof ItemId && !$wdId instanceof NumericPropertyId ) {
			$this->lastError = 'unsupported entity type';
			return null;
		}
		$remote = $this->remote( $wd );
		if ( !$remote ) {
			$this->lastError = 'not found on Wikidata';
			return null;
		}
		if ( $remote instanceof Property ) {
			$entity = Property::newFromType( $remote->getDataTypeId() );
		} else {
			$entity = new Item();
		}
		foreach ( self::LANGS as $lang ) {
			if ( $remote->getLabels()->hasTermForLanguage( $lang ) ) {
				$entity->setLabel( $lang, $remote->getLabels()->getByLanguage( $lang )->getText() );
			}
			if ( $remote->getDescriptions()->hasTermForLanguage( $lang ) ) {
				$entity->setDescription( $lang, $remote->getDescriptions()->getByLanguage( $lang )->getText() );
			}
		}
		if ( $remote instanceof Item ) {
			foreach ( self::WIKIS as $site ) {
				if ( $remote->getSiteLinkList()->hasLinkWithSiteId( $site ) ) {
					$title = $remote->getSiteLinkList()->getBySiteId( $site )->getPageName();
					$entity->getSiteLinkList()->addSiteLink( new SiteLink( $site, $title ) );
				}
			}
		}
		try {
			$saved = WikibaseRepo::getEntityStore()->saveEntity( $entity, "Import $wd from Wikidata", $this->user, EDIT_NEW );
		} catch ( Exception $e ) {
			$this->lastError = str_replace( [ "\r", "\n" ], ' ', $e->getMessage() );
			return null;
		}
		$local = $saved->getEntity()->getId()->getSerialization();
		$this->dbw()->newInsertQueryBuilder()
			->insertInto( 'wbs_entity_mapping' )
			->row( [ 'wbs_local_id' => $local, 'wbs_original_id' => $wd ] )
			->caller( __METHOD__ )
			->execute();
		return $local;
	}

	/**
	 * Handles --import; prints one IMPORT line per ID.
	 * @param array<string, true> &$failed receives IDs that failed
	 * @return string[] local item IDs (mapped or created), to be enriched
	 */
	private function runImports( array &$failed ): array {
		$items = [];
		foreach ( array_unique( (array)$this->getOption( 'import', [] ) ) as $wd ) {
			$wd = strtoupper( trim( $wd ) );
			if ( preg_match( '/^Q[1-9]\d*$/', $wd ) ) {
				$wdId = new ItemId( $wd );
			} elseif ( preg_match( '/^P[1-9]\d*$/', $wd ) ) {
				$wdId = new NumericPropertyId( $wd );
			} else {
				$this->output( "IMPORT $wd FAILED invalid ID\n" );
				$failed[$wd] = true;
				continue;
			}
			$local = $this->localId( $wd );
			if ( $local ) {
				$this->output( "IMPORT $wd = $local (mapped)\n" );
			} elseif ( $this->dryRun ) {
				$this->output( "IMPORT $wd = (would create)\n" );
				continue;
			} else {
				$local = $this->importEntity( $wdId );
				if ( $local === null ) {
					$this->output( "IMPORT $wd FAILED {$this->lastError}\n" );
					$failed[$wd] = true;
					continue;
				}
				$this->output( "IMPORT $wd = $local (created)\n" );
			}
			if ( $wdId instanceof ItemId ) {
				$items[] = $local;
			}
		}
		return $items;
	}

	private function remote( string $wd ): Item|Property|null {
		if ( !array_key_exists( $wd, $this->remoteCache ) ) {
			$url = wfAppendQuery( self::SOURCE, [
				'action' => 'wbgetentities', 'ids' => $wd, 'format' => 'json',
				'props' => 'labels|descriptions|claims|sitelinks|datatype',
			] );
			$json = MediaWikiServices::getInstance()->getHttpRequestFactory()->get( $url, [], __METHOD__ );
			$data = $json ? json_decode( $json, true ) : null;
			$serialization = $data['entities'][$wd] ?? null;
			$this->remoteCache[$wd] = null;
			if ( $serialization && !isset( $serialization['missing'] ) ) {
				$entity = WikibaseRepo::getBaseDataModelDeserializerFactory()
					->newEntityDeserializer()->deserialize( $serialization );
				$this->remoteCache[$wd] = $entity instanceof Item || $entity instanceof Property ? $entity : null;
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

	/**
	 * @param string[] $imported local item IDs from --import
	 * @return string[] local item IDs
	 */
	private function localItems( array $imported = [] ): array {
		$items = array_merge( (array)$this->getOption( 'item', [] ), $imported );
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
		$items = array_values( array_unique( $items ) );
		$follow = $this->getOption( 'follow' );
		if ( $follow ) {
			$props = array_filter( array_map( 'trim', explode( ',', $follow ) ) );
			foreach ( $props as $prop ) {
				if ( !preg_match( '/^P[1-9]\d*$/', $prop ) ) {
					$this->fatalError( "--follow: invalid property ID $prop" );
				}
			}
			foreach ( $items as $localSerialization ) {
				$wd = $this->originalId( $localSerialization );
				$remote = $wd ? $this->remote( $wd ) : null;
				if ( !$remote instanceof Item ) {
					continue;
				}
				foreach ( $props as $prop ) {
					$statements = $remote->getStatements()->getByPropertyId( new NumericPropertyId( $prop ) );
					foreach ( $statements->toArray() as $statement ) {
						$snak = $statement->getMainSnak();
						if ( !$snak instanceof PropertyValueSnak || $statement->getRank() === Statement::RANK_DEPRECATED ) {
							continue;
						}
						$value = $snak->getDataValue();
						if ( !$value instanceof EntityIdValue || !$value->getEntityId() instanceof ItemId ) {
							continue;
						}
						// Unmapped targets are created here; on a dry run they are left out (nothing is written).
						$target = $this->ensureLocal( $value->getEntityId() );
						if ( $target !== null ) {
							$items[] = $target;
						}
					}
				}
			}
			$items = array_values( array_unique( $items ) );
		}
		return $items;
	}
}

$maintClass = EnrichItems::class;
require_once RUN_MAINTENANCE_IF_MAIN;
