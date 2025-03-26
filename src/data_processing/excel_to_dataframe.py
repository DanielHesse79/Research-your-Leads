import pandas as pd
import os
import logging
import re
from typing import Dict, List, Optional, Union

# Konfigurera loggning
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ExcelProcessor:
    """Klass för att hantera import av Excel-filer från olika källor och konvertera till Pandas DataFrame."""
    
    def __init__(self, source_directory: str):
        """Initiera processor med sökväg till källkatalog för Excel-filer."""
        self.source_directory = source_directory
        logger.info(f"Excel-processor initierad med källkatalog: {source_directory}")
    
    def list_available_files(self) -> List[str]:
        """Listar alla tillgängliga Excel-filer i källkatalogen."""
        excel_files = []
        for file in os.listdir(self.source_directory):
            if file.endswith(('.xlsx', '.xls', '.csv')):
                excel_files.append(os.path.join(self.source_directory, file))
        logger.info(f"Hittade {len(excel_files)} Excel-filer i källkatalogen")
        return excel_files
    
    def read_excel_file(self, file_path: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        Läser in en Excel-fil med inställningen att hoppa över den första raden (ingen relevant info)
        så att rubrikerna från rad 2 används.
        """
        try:
            # Hoppa över första raden med skiprows=1
            kwargs.setdefault("skiprows", 1)
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path, **kwargs)
            else:
                df = pd.read_excel(file_path, **kwargs)
            
            logger.info(f"Läste in fil {file_path} med {len(df)} rader")
            return df
        except Exception as e:
            logger.error(f"Fel vid inläsning av {file_path}: {str(e)}")
            return None

    def extract_researcher_data(self, cell_text: Union[str, None], external_email: Optional[str] = None, pmid: Optional[str] = None) -> List[Dict]:
        """
        Extraherar forskardata från en cell med text som innehåller flera poster i formatet:
        "Namn (Affiliation)". Texten kan innehålla extra semikolon eller radbrytningar.
        Om ett email finns med (antingen inbäddat eller externt) plockas det ut.
        PMID bifogas till varje post.
        """
        entries = []
        if not isinstance(cell_text, str):
            return entries
        
        # Dela upp texten med semikolon eller radbrytningar
        parts = [part.strip() for part in re.split(r';|\n', cell_text) if part.strip()]
        
        for part in parts:
            # Försök hitta email i texten
            email_match = re.search(r'[\w\.-]+@[\w\.-]+', part)
            found_email = email_match.group(0) if email_match else external_email
            # Ta bort email ur texten om den finns
            if found_email:
                part = part.replace(found_email, '')
            
            # Leta efter mönstret "Namn (Affiliation)"
            match = re.search(r'(.+?)\s*\(([^)]+)\)', part)
            if match:
                name = match.group(1).strip()
                affiliation = match.group(2).strip()
                entries.append({
                    "name": name,
                    "affiliation": affiliation,
                    "email": found_email,
                    "pmid": pmid
                })
            else:
                # Om inget mönster hittas, spara hela texten som namn
                entries.append({
                    "name": part.strip(),
                    "affiliation": None,
                    "email": found_email,
                    "pmid": pmid
                })
        return entries

    def process_dataframe(self, df: pd.DataFrame, researcher_col: str = "X", pmid_col: str = "D", email_col: Optional[str] = None) -> pd.DataFrame:
        """
        Bearbetar DataFrame genom att gå igenom varje rad, extrahera forskarinformation från en angiven kolumn 
        (där det kan finnas flera poster) och lägga till PubMed ID från en annan kolumn.
        
        Parametrar:
            researcher_col: Namnet på kolumnen där forskardata (text) finns (här kolumn X).
            pmid_col: Namnet på kolumnen med PubMed-ID (här kolumn D).
            email_col: Valfri kolumn för e-post om den finns separat (sätt till None om email endast finns inbäddat).
            
        Returnerar en DataFrame med en post per forskare.
        """
        processed_entries = []
        
        for index, row in df.iterrows():
            # Hämta den stökiga cellen med forskardata från kolumn X
            cell_text = row.get(researcher_col, "")
            # Om email finns separat i en kolumn, sätt den; annars None
            external_email = row.get(email_col) if email_col and pd.notna(row.get(email_col)) else None
            # Hämta PMID från kolumn D
            pmid = str(row.get(pmid_col)) if pmid_col in row and pd.notna(row.get(pmid_col)) else None
            
            researcher_entries = self.extract_researcher_data(cell_text, external_email=external_email, pmid=pmid)
            processed_entries.extend(researcher_entries)
        
        processed_df = pd.DataFrame(processed_entries)
        logger.info(f"Extraherade {len(processed_df)} forskarposter från DataFrame med {len(df)} rader")
        return processed_df

    def process_batch(self, file_paths: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        """Bearbetar en batch av Excel-filer och returnerar dictionary med DataFrame för varje fil."""
        result = {}
        if file_paths is None:
            file_paths = self.list_available_files()
        
        for file_path in file_paths:
            df = self.read_excel_file(file_path)
            if df is not None:
                # Använd kolumn X för forskardata och D för PubMed ID.
                processed_df = self.process_dataframe(df, researcher_col="X", pmid_col="D", email_col=None)
                result[os.path.basename(file_path)] = processed_df
        logger.info(f"Bearbetade {len(result)} av {len(file_paths)} Excel-filer")
        return result

# Exempel på användning
if __name__ == "__main__":
    processor = ExcelProcessor("./data/raw")
    dataframes = processor.process_batch()
    for file_name, df in dataframes.items():
        print(f"Fil: {file_name} -> {len(df)} forskarposter extraherade")
