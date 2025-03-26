from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import logging
from typing import Optional, Dict, List, Any
import pandas as pd
from config.database import get_database_url

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create declarative base for SQLAlchemy models
Base = declarative_base()

class BaseDatabase:
    """Base class for database operations supporting both SQLite and PostgreSQL."""
    
    def __init__(self, db_type: str = 'default'):
        """Initialize database connection."""
        self.db_type = db_type
        self.engine = create_engine(get_database_url(db_type))
        self.Session = sessionmaker(bind=self.engine)
        self._initialize_database()
        logger.info(f"BaseDatabase initialized with {db_type} database")
    
    def _initialize_database(self):
        """Initialize database tables."""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Database tables initialized")
        except Exception as e:
            logger.error(f"Error initializing database: {str(e)}")
            raise
    
    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """Execute a SQL query and return results as a list of dictionaries."""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), params or {})
                return [dict(row) for row in result]
        except Exception as e:
            logger.error(f"Error executing query: {str(e)}")
            raise
    
    def execute_many(self, query: str, params_list: List[Dict]) -> None:
        """Execute a SQL query multiple times with different parameters."""
        try:
            with self.engine.connect() as conn:
                for params in params_list:
                    conn.execute(text(query), params)
                conn.commit()
        except Exception as e:
            logger.error(f"Error executing batch query: {str(e)}")
            raise
    
    def store_dataframe(self, df: pd.DataFrame, table_name: str, if_exists: str = 'replace') -> bool:
        """Store a pandas DataFrame in the database."""
        try:
            df.to_sql(table_name, self.engine, if_exists=if_exists, index=False)
            logger.info(f"DataFrame stored in table {table_name} with {len(df)} rows")
            return True
        except Exception as e:
            logger.error(f"Error storing DataFrame: {str(e)}")
            return False
    
    def read_dataframe(self, query: str, params: Optional[Dict] = None) -> pd.DataFrame:
        """Read data from database into a pandas DataFrame."""
        try:
            return pd.read_sql_query(query, self.engine, params=params)
        except Exception as e:
            logger.error(f"Error reading DataFrame: {str(e)}")
            raise
    
    def get_session(self):
        """Get a new database session."""
        return self.Session()
    
    def close_session(self, session):
        """Close a database session."""
        if session:
            session.close() 