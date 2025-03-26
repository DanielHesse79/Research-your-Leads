import os
import pandas as pd
import logging
import argparse
import sys
from pathlib import Path

# Lägg till src-katalogen till Python-sökvägen
sys.path.append(str(Path(__file__).parent.parent))

# Importera komponenter
from data_processing.excel_to_dataframe import ExcelProcessor
from database.staging_db import StagingDatabase, DataValidator
from database.permanent_db import PermanentDatabase
from external_data.data_collector import PubMedCollector, GoogleScholarCollector, OrcidClient

# Konfigurera loggning
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def setup_directories():
    """Skapa nödvändiga kataloger om de inte finns."""
    base_dir = Path(__file__).parent.parent
    directories = [
        base_dir / "data",
        base_dir / "data" / "raw",
        base_dir / "data" / "processed",
        base_dir / "data" / "external"
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        logger.info(f"Katalog skapad: {directory}")

def process_excel_to_staging(excel_dir, staging_db_path, validation_rules_path=None):
    """Läs in Excel-filer, validera dem och lagra i staging-databasen."""
    # Initiera validator och staging-databas
    validator = DataValidator()
    if validation_rules_path and os.path.exists(validation_rules_path):
        validator.load_validation_rules(validation_rules_path)
    
    staging_db = StagingDatabase(staging_db_path, validator)
    
    # Initiera Excel-processor och läs filer
    processor = ExcelProcessor(excel_dir)
    dataframes = processor.process_batch()
    
    # Lagra varje dataframe i staging-databasen
    dataset_ids = []
    for file_name, df in dataframes.items():
        schema_name = Path(file_name).stem  # Använd filnamn utan ändelse som schema
        dataset_id = staging_db.store_dataframe(df, schema_name, schema_name)
        if dataset_id > 0:
            dataset_ids.append(dataset_id)
            logger.info(f"Dataframe från {file_name} lagrad med dataset-ID: {dataset_id}")
        else:
            logger.error(f"Fel vid lagring av dataframe från {file_name}")
    
    return dataset_ids

def approve_and_move_to_permanent(dataset_id, staging_db_path, permanent_db_path):
    """Godkänn ett dataset och flytta det till den permanenta databasen."""
    staging_db = StagingDatabase(staging_db_path)
    permanent_db = PermanentDatabase(permanent_db_path)
    
    # Hämta dataset-information från staging
    dataset_info = staging_db.get_dataset_info(dataset_id)
    if not dataset_info:
        logger.error(f"Dataset med ID {dataset_id} hittades inte i staging-databasen")
        return False
    
    dataset = dataset_info[0]
    logger.info(f"Flyttar dataset '{dataset['name']}' till permanent databas")
    
    # Läs data från staging-databasen
    import sqlite3
    with sqlite3.connect(staging_db_path) as conn:
        df = pd.read_sql_query(f"SELECT * FROM {dataset['name']}", conn)
    
    # Lagra i permanent databas
    permanent_id = permanent_db.store_dataframe(df, dataset['name'], dataset['source'])
    
    if permanent_id > 0:
        # Uppdatera status i staging-databasen
        with sqlite3.connect(staging_db_path) as conn:
            conn.execute("UPDATE datasets SET status = 'approved' WHERE id = ?", (dataset_id,))
        
        logger.info(f"Dataset flyttat till permanent databas med ID: {permanent_id}")
        return True
    else:
        logger.error(f"Fel vid flyttning av dataset till permanent databas")
        return False

def match_researchers_with_orcid(permanent_db_path, name_column, keywords_column=None, institution_column=None):
    """Matcha forskare i databasen mot ORCID-ID:n."""
    permanent_db = PermanentDatabase(permanent_db_path)
    orcid_client = OrcidClient()
    
    # Hämta alla dataset från permanent databas
    datasets = permanent_db.get_dataset_info()
    
    for dataset in datasets:
        # Kontrollera om dataset redan har ORCID-kopplingar
        if dataset.get('orcid_linked'):
            logger.info(f"Dataset '{dataset['name']}' har redan ORCID-kopplingar")
            continue
        
        # Hämta data från databasen
        df = permanent_db.query_data(f"SELECT * FROM {dataset['name']}")
        if df is None or name_column not in df.columns:
            logger.warning(f"Kolumnen '{name_column}' saknas i dataset '{dataset['name']}'")
            continue
        
        # Bearbeta varje rad för att matcha forskare
        match_count = 0
        for idx, row in df.iterrows():
            name = row[name_column]
            if not name or pd.isna(name):
                continue
            
            # Extrahera ytterligare information om tillgänglig
            keywords = None
            if keywords_column and keywords_column in df.columns and not pd.isna(row[keywords_column]):
                keywords = [kw.strip() for kw in str(row[keywords_column]).split(',')]
            
            institution = None
            if institution_column and institution_column in df.columns and not pd.isna(row[institution_column]):
                institution = row[institution_column]
            
            # Försök matcha forskare
            researcher = orcid_client.match_researcher(name, keywords, institution)
            if researcher and researcher.get('orcid'):
                # Registrera ORCID-koppling i databasen
                record_id = str(idx)  # Använd dataframe-index som record_id
                if 'id' in df.columns:
                    record_id = str(row['id'])
                
                confidence = researcher.get('match_confidence', 0.5)
                success = permanent_db.register_orcid_mapping(
                    dataset['id'], record_id, researcher['orcid'], confidence
                )
                
                if success:
                    match_count += 1
                    logger.info(f"Matchade '{name}' till ORCID: {researcher['orcid']}")
                else:
                    logger.error(f"Fel vid registrering av ORCID-koppling för '{name}'")
        
        logger.info(f"Dataset '{dataset['name']}': Matchade {match_count} av {len(df)} forskare")

def collect_external_data(permanent_db_path, orcid=None, query=None, max_results=10):
    """Samla extern data från PubMed baserat på ORCID eller sökfråga."""
    permanent_db = PermanentDatabase(permanent_db_path)
    pubmed = PubMedCollector()
    
    # Hämta artiklar
    articles = []
    if orcid:
        logger.info(f"Söker efter artiklar för ORCID: {orcid}")
        articles = pubmed.search_by_orcid(orcid, max_results)
    elif query:
        logger.info(f"Söker efter artiklar med fråga: {query}")
        articles = pubmed.search_articles(query, max_results)
    else:
        logger.error("Ingen ORCID eller sökfråga angiven")
        return
    
    # Konvertera till DataFrame och lagra i permanent databas
    if articles:
        df = pubmed.to_dataframe(articles)
        
        # Generera tabellnamn
        table_name = "pubmed_data"
        if orcid:
            table_name = f"pubmed_orcid_{orcid.replace('-', '_')}"
        elif query:
            table_name = f"pubmed_query_{query.replace(' ', '_')[:30]}"
        
        # Lagra i databasen
        dataset_id = permanent_db.store_dataframe(df, table_name, "pubmed_api")
        if dataset_id > 0:
            logger.info(f"Lagrade {len(df)} artiklar från PubMed i tabellen '{table_name}'")
            
            # Om ORCID angavs, skapa en relation till forskaren
            if orcid:
                # Hämta alla dataset som har denna ORCID
                orcid_mappings = permanent_db.get_orcid_mappings(orcid=orcid)
                for mapping in orcid_mappings:
                    permanent_db.register_dataset_relationship(
                        mapping['dataset_id'], dataset_id, "author_publications"
                    )
                    logger.info(f"Registrerade relation mellan dataset {mapping['dataset_id']} och publikationsdata {dataset_id}")
        else:
            logger.error("Fel vid lagring av artiklar från PubMed")
    else:
        logger.warning("Inga artiklar hittades")

def main():
    """Huvudfunktion som orchestrerar flödet."""
    parser = argparse.ArgumentParser(description="Dataflödessystem för forskningsdata")
    
    # Definiera argument
    parser.add_argument('--setup', action='store_true', help='Skapa kataloger')
    parser.add_argument('--process-excel', action='store_true', help='Bearbeta Excel-filer till staging-databas')
    parser.add_argument('--excel-dir', default='./data/raw', help='Katalog med Excel-filer')
    parser.add_argument('--validation-rules', default='./config/validation_rules.json', help='Sökväg till valideringsregler')
    parser.add_argument('--staging-db', default='./data/staging.db', help='Sökväg till staging-databas')
    parser.add_argument('--permanent-db', default='./data/permanent.db', help='Sökväg till permanent databas')
    parser.add_argument('--approve-dataset', type=int, help='Dataset-ID att godkänna och flytta')
    parser.add_argument('--match-orcid', action='store_true', help='Matcha forskare mot ORCID-ID')
    parser.add_argument('--name-column', default='name', help='Kolumnnamn för forskarens namn')
    parser.add_argument('--keywords-column', help='Kolumnnamn för nyckelord/forskningsområden')
    parser.add_argument('--institution-column', help='Kolumnnamn för institution')
    parser.add_argument('--collect-pubmed', action='store_true', help='Samla data från PubMed')
    parser.add_argument('--orcid', help='ORCID-ID att söka efter')
    parser.add_argument('--query', help='Sökfråga för PubMed')
    parser.add_argument('--max-results', type=int, default=10, help='Maximalt antal resultat att hämta')
    
    args = parser.parse_args()
    
    # Skapa kataloger om det behövs
    if args.setup:
        setup_directories()
    
    # Bearbeta Excel-filer till staging-databas
    if args.process_excel:
        excel_dir = Path(args.excel_dir)
        if not excel_dir.exists() or not any(excel_dir.glob('*.xls*')):
            logger.error(f"Inga Excel-filer hittades i {excel_dir}")
        else:
            process_excel_to_staging(
                args.excel_dir, 
                args.staging_db,
                args.validation_rules if os.path.exists(args.validation_rules) else None
            )
    
    # Godkänn och flytta dataset till permanent databas
    if args.approve_dataset:
        approve_and_move_to_permanent(
            args.approve_dataset,
            args.staging_db,
            args.permanent_db
        )
    
    # Matcha forskare mot ORCID-ID
    if args.match_orcid:
        match_researchers_with_orcid(
            args.permanent_db,
            args.name_column,
            args.keywords_column,
            args.institution_column
        )
    
    # Samla data från PubMed
    if args.collect_pubmed:
        collect_external_data(
            args.permanent_db,
            args.orcid,
            args.query,
            args.max_results
        )

if __name__ == "__main__":
    main() 