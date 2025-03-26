import pandas as pd
import sqlite3
import logging
import os
from typing import Dict, List, Optional, Any, Tuple
import json
import datetime

# Konfigurera loggning
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PermanentDatabase:
    """Klass för att hantera interaktion med den permanenta databasen där godkänd data lagras."""
    
    def __init__(self, db_path: str):
        """Initiera databasanslutning till permanent databas."""
        self.db_path = db_path
        
        # Skapa databasen och tabeller om de inte finns
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._initialize_database()
        logger.info(f"PermanentDatabase initierad med databas på {db_path}")
    
    def _initialize_database(self):
        """Initiera databasen med nödvändiga tabeller."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Tabell för att lagra metadata om godkända dataset
                conn.execute('''
                CREATE TABLE IF NOT EXISTS datasets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    source TEXT,
                    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved_date TIMESTAMP,
                    record_count INTEGER,
                    orcid_linked BOOLEAN DEFAULT 0,
                    UNIQUE(name, source)
                )
                ''')
                
                # Tabell för att lagra metadata om dataset-relationer
                conn.execute('''
                CREATE TABLE IF NOT EXISTS dataset_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_dataset_id INTEGER,
                    target_dataset_id INTEGER,
                    relationship_type TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_dataset_id) REFERENCES datasets(id),
                    FOREIGN KEY (target_dataset_id) REFERENCES datasets(id)
                )
                ''')
                
                # Tabell för ORCID-matchningar
                conn.execute('''
                CREATE TABLE IF NOT EXISTS orcid_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_id INTEGER,
                    record_id TEXT,
                    orcid TEXT,
                    match_confidence REAL,
                    match_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (dataset_id) REFERENCES datasets(id)
                )
                ''')
                
                logger.info("Permanenta databastabeller initierade")
        except Exception as e:
            logger.error(f"Fel vid initiering av permanent databas: {str(e)}")
    
    def store_dataframe(self, df: pd.DataFrame, table_name: str, source: str = None) -> int:
        """Lagra en godkänd dataframe i den permanenta databasen."""
        dataset_id = -1
        
        try:
            # Lagra data i databasen
            with sqlite3.connect(self.db_path) as conn:
                # Registrera dataset
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO datasets (name, source, approved_date, record_count) VALUES (?, ?, ?, ?)",
                    (table_name, source, datetime.datetime.now().isoformat(), len(df))
                )
                dataset_id = cursor.lastrowid
                
                # Lagra dataframe
                df.to_sql(table_name, conn, if_exists='replace', index=False)
                logger.info(f"DataFrame lagrad i permanent databas, tabell {table_name} med {len(df)} rader")
                
                return dataset_id
        except Exception as e:
            logger.error(f"Fel vid lagring av DataFrame i permanent databas: {str(e)}")
            return -1
    
    def get_dataset_info(self, dataset_id: int = None) -> List[Dict]:
        """Hämta information om datasets i permanent databas."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if dataset_id is not None:
                    cursor.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
                else:
                    cursor.execute("SELECT * FROM datasets ORDER BY approved_date DESC")
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Fel vid hämtning av dataset-information från permanent databas: {str(e)}")
            return []
    
    def register_orcid_mapping(self, dataset_id: int, record_id: str, orcid: str, confidence: float) -> bool:
        """Registrera en ORCID-koppling för en post i ett dataset."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO orcid_mappings (dataset_id, record_id, orcid, match_confidence) VALUES (?, ?, ?, ?)",
                    (dataset_id, record_id, orcid, confidence)
                )
                
                # Uppdatera dataset att det har ORCID-kopplingar
                cursor.execute("UPDATE datasets SET orcid_linked = 1 WHERE id = ?", (dataset_id,))
                
                logger.info(f"ORCID-koppling registrerad för dataset {dataset_id}, post {record_id}, ORCID: {orcid}")
                return True
        except Exception as e:
            logger.error(f"Fel vid registrering av ORCID-koppling: {str(e)}")
            return False
    
    def get_orcid_mappings(self, dataset_id: int = None, orcid: str = None) -> List[Dict]:
        """Hämta ORCID-kopplingar med filtrering på dataset-id eller ORCID."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if dataset_id is not None and orcid is not None:
                    cursor.execute("SELECT * FROM orcid_mappings WHERE dataset_id = ? AND orcid = ?", (dataset_id, orcid))
                elif dataset_id is not None:
                    cursor.execute("SELECT * FROM orcid_mappings WHERE dataset_id = ?", (dataset_id,))
                elif orcid is not None:
                    cursor.execute("SELECT * FROM orcid_mappings WHERE orcid = ?", (orcid,))
                else:
                    cursor.execute("SELECT * FROM orcid_mappings ORDER BY match_date DESC")
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Fel vid hämtning av ORCID-kopplingar: {str(e)}")
            return []
    
    def register_dataset_relationship(self, source_id: int, target_id: int, relationship_type: str) -> bool:
        """Registrera en relation mellan två datasets."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO dataset_relationships (source_dataset_id, target_dataset_id, relationship_type) VALUES (?, ?, ?)",
                    (source_id, target_id, relationship_type)
                )
                
                logger.info(f"Dataset-relation registrerad: {source_id} -> {target_id}, typ: {relationship_type}")
                return True
        except Exception as e:
            logger.error(f"Fel vid registrering av dataset-relation: {str(e)}")
            return False
    
    def get_dataset_relationships(self, dataset_id: int = None) -> List[Dict]:
        """Hämta relationerna för ett specifikt dataset eller alla relationer."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if dataset_id is not None:
                    cursor.execute("""
                        SELECT * FROM dataset_relationships 
                        WHERE source_dataset_id = ? OR target_dataset_id = ?
                    """, (dataset_id, dataset_id))
                else:
                    cursor.execute("SELECT * FROM dataset_relationships")
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Fel vid hämtning av dataset-relationer: {str(e)}")
            return []
    
    def query_data(self, sql_query: str) -> Optional[pd.DataFrame]:
        """Kör en SQL-query mot den permanenta databasen och returnerar resultatet som DataFrame."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                df = pd.read_sql_query(sql_query, conn)
                logger.info(f"SQL-query kördes med {len(df)} resultatrader")
                return df
        except Exception as e:
            logger.error(f"Fel vid körning av SQL-query: {str(e)}")
            return None

# Exempel på användning
if __name__ == "__main__":
    db = PermanentDatabase("./data/permanent.db")
    
    # Exempel på att lagra en dataframe
    df = pd.DataFrame({
        'name': ['Anna', 'Bertil', 'Cecilia'],
        'age': [34, 45, 28],
        'email': ['anna@example.com', 'bertil@example.com', 'cecilia@example.com']
    })
    
    dataset_id = db.store_dataframe(df, 'test_data', 'manual_import')
    if dataset_id > 0:
        print(f"Dataset lagrat med ID: {dataset_id}")
        
        # Registrera några ORCID-kopplingar
        db.register_orcid_mapping(dataset_id, "Anna", "0000-0001-1234-5678", 0.95)
        
        # Hämta ORCID-kopplingar
        mappings = db.get_orcid_mappings(dataset_id)
        print(f"ORCID-kopplingar för dataset {dataset_id}:")
        for mapping in mappings:
            print(f"  {mapping['record_id']}: {mapping['orcid']} (konfidens: {mapping['match_confidence']})") 