import pandas as pd
import sqlite3
import logging
from typing import List, Dict, Optional, Any, Tuple
import json
import os

# Konfigurera loggning
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DataValidator:
    """Klass för att validera och normalisera dataframes innan de sparas i staging-databasen."""
    
    def __init__(self, validation_rules: Optional[Dict[str, Dict]] = None):
        """Initiera validator med valideringsregler."""
        self.validation_rules = validation_rules or {}
        logger.info("DataValidator initierad")
    
    def load_validation_rules(self, rules_file: str):
        """Ladda valideringsregler från JSON-fil."""
        try:
            with open(rules_file, 'r', encoding='utf-8') as f:
                self.validation_rules = json.load(f)
            logger.info(f"Valideringsregler laddade från {rules_file}")
        except Exception as e:
            logger.error(f"Fel vid laddning av valideringsregler: {str(e)}")
    
    def validate_dataframe(self, df: pd.DataFrame, schema_name: str) -> Tuple[pd.DataFrame, List[Dict]]:
        """Validera en dataframe mot specifika regler och normalisera data."""
        if schema_name not in self.validation_rules:
            logger.warning(f"Inga valideringsregler hittades för schema {schema_name}")
            return df, []
        
        errors = []
        rules = self.validation_rules[schema_name]
        
        # Validera och normalisera varje kolumn enligt reglerna
        for column, rule in rules.items():
            if column not in df.columns:
                logger.warning(f"Kolumn {column} saknas i DataFrame")
                continue
            
            # Kontrollera datatyp
            if 'type' in rule:
                try:
                    if rule['type'] == 'float':
                        df[column] = pd.to_numeric(df[column], errors='coerce')
                    elif rule['type'] == 'int':
                        df[column] = pd.to_numeric(df[column], errors='coerce').astype('Int64')  # Nullable int type
                    elif rule['type'] == 'date':
                        df[column] = pd.to_datetime(df[column], errors='coerce')
                except Exception as e:
                    logger.error(f"Fel vid typkonvertering för {column}: {str(e)}")
                    errors.append({'column': column, 'error': 'type_conversion', 'details': str(e)})
            
            # Kontrollera obligatoriska fält
            if rule.get('required', False):
                null_count = df[column].isna().sum()
                if null_count > 0:
                    errors.append({
                        'column': column, 
                        'error': 'missing_values', 
                        'details': f"{null_count} rader saknar värden"
                    })
            
            # Kontrollera unika fält
            if rule.get('unique', False):
                if not df[column].is_unique:
                    duplicates = df[column].duplicated().sum()
                    errors.append({
                        'column': column, 
                        'error': 'duplicate_values', 
                        'details': f"{duplicates} duplicerade värden"
                    })
        
        return df, errors


class StagingDatabase:
    """Klass för att hantera interaktion med staging-databasen."""
    
    def __init__(self, db_path: str, validator: Optional[DataValidator] = None):
        """Initiera databasanslutning och validator."""
        self.db_path = db_path
        self.validator = validator or DataValidator()
        
        # Skapa databasen och tabeller om de inte finns
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._initialize_database()
        logger.info(f"StagingDatabase initierad med databas på {db_path}")
    
    def _initialize_database(self):
        """Initiera databasen med nödvändiga tabeller."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Tabell för att lagra metadata om dataset
                conn.execute('''
                CREATE TABLE IF NOT EXISTS datasets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    source TEXT,
                    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'imported',
                    record_count INTEGER,
                    UNIQUE(name, source)
                )
                ''')
                
                # Tabell för att lagra valideringsfel
                conn.execute('''
                CREATE TABLE IF NOT EXISTS validation_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_id INTEGER,
                    column_name TEXT,
                    error_type TEXT,
                    details TEXT,
                    FOREIGN KEY (dataset_id) REFERENCES datasets(id)
                )
                ''')
                
                logger.info("Databastabeller initierade")
        except Exception as e:
            logger.error(f"Fel vid initiering av databas: {str(e)}")
    
    def store_dataframe(self, df: pd.DataFrame, table_name: str, schema_name: str = None) -> int:
        """Validera och lagra en dataframe i staging-databasen."""
        dataset_id = -1
        
        try:
            # Validera data om schema anges
            if schema_name:
                df, errors = self.validator.validate_dataframe(df, schema_name)
            else:
                errors = []
            
            # Lagra data i databasen
            with sqlite3.connect(self.db_path) as conn:
                # Registrera dataset
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO datasets (name, source, record_count) VALUES (?, ?, ?)",
                    (table_name, schema_name, len(df))
                )
                dataset_id = cursor.lastrowid
                
                # Lagra valideringsfel
                for error in errors:
                    cursor.execute(
                        "INSERT INTO validation_errors (dataset_id, column_name, error_type, details) VALUES (?, ?, ?, ?)",
                        (dataset_id, error['column'], error['error'], error.get('details', ''))
                    )
                
                # Lagra dataframe
                df.to_sql(table_name, conn, if_exists='replace', index=False)
                logger.info(f"DataFrame lagrad i tabell {table_name} med {len(df)} rader")
                
                return dataset_id
        except Exception as e:
            logger.error(f"Fel vid lagring av DataFrame: {str(e)}")
            return -1
    
    def get_dataset_info(self, dataset_id: int = None) -> List[Dict]:
        """Hämta information om datasets i staging-databasen."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if dataset_id is not None:
                    cursor.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
                else:
                    cursor.execute("SELECT * FROM datasets ORDER BY import_date DESC")
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Fel vid hämtning av dataset-information: {str(e)}")
            return []
    
    def get_validation_errors(self, dataset_id: int) -> List[Dict]:
        """Hämta valideringsfel för ett specifikt dataset."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM validation_errors WHERE dataset_id = ?", (dataset_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Fel vid hämtning av valideringsfel: {str(e)}")
            return []

# Exempel på användning
if __name__ == "__main__":
    validator = DataValidator()
    staging_db = StagingDatabase("./data/staging.db", validator)
    
    # Exempel på dataframe
    df = pd.DataFrame({
        'name': ['Anna', 'Bertil', 'Cecilia'],
        'age': ['34', '45', 'invalid'],
        'email': ['anna@example.com', 'bertil@example.com', None]
    })
    
    # Exempel på valideringsregler
    rules = {
        'test_schema': {
            'name': {'type': 'str', 'required': True},
            'age': {'type': 'int'},
            'email': {'type': 'str', 'required': True}
        }
    }
    
    validator.validation_rules = rules
    dataset_id = staging_db.store_dataframe(df, 'test_data', 'test_schema')
    
    if dataset_id > 0:
        print("Dataset lagrad. Valideringsfel:")
        errors = staging_db.get_validation_errors(dataset_id)
        for error in errors:
            print(f"{error['column_name']}: {error['error_type']} - {error['details']}") 