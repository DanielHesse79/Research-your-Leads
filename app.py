import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import requests
import time
import json
import datetime
import os
from src.database.staging_db import StagingDatabase, DataValidator
from src.database.permanent_db import PermanentDatabase
from src.external_data.data_collector import OrcidClient, PubMedCollector
from bs4 import BeautifulSoup
import re

# Skapa datakataloger om de inte finns
os.makedirs("data", exist_ok=True)

# Konfigurera validator och databaser
validator = DataValidator()
config_dir = "config"
if os.path.exists(os.path.join(config_dir, "validation_rules.json")):
    validator.load_validation_rules(os.path.join(config_dir, "validation_rules.json"))

# Lägg till cache-dekorator för att förhindra upprepade initialiseringar
@st.cache_resource
def init_db_connections():
    """Initialisera databaskopplingar med caching för att förhindra upprepade initialiseringar."""
    try:
        staging_db = StagingDatabase(db_path="./data/staging.db")
        permanent_db = PermanentDatabase(db_path="./data/permanent.db")
        
        # Skapa SQLAlchemy-kopplingar för direkta SQL-frågor
        staging_engine = create_engine(f"sqlite:///./data/staging.db")
        permanent_engine = create_engine(f"sqlite:///./data/permanent.db")
        
        # Skapa också ORCID och PubMed-klienter här så de inte återskapas hela tiden
        orcid_client = OrcidClient()
        pubmed_collector = PubMedCollector()
        
        return staging_db, permanent_db, staging_engine, permanent_engine, orcid_client, pubmed_collector
    except Exception as e:
        st.error(f"Fel vid initialisering av databaskopplingar: {str(e)}")
        raise e

# Anropa den cachade funktionen för att få alla databas-komponenter
staging_db, permanent_db, staging_engine, permanent_engine, orcid_client, pubmed_collector = init_db_connections()

# Förbered hjälpvariabler och sessionsvariabler
if 'selected_researcher_id' not in st.session_state:
    st.session_state['selected_researcher_id'] = None

if 'pubmed_results' not in st.session_state:
    st.session_state['pubmed_results'] = []

if 'current_view' not in st.session_state:
    st.session_state['current_view'] = "search"

if 'search_history' not in st.session_state:
    st.session_state['search_history'] = []

if 'last_orcid_search' not in st.session_state:
    st.session_state.last_orcid_search = ""
    st.session_state.last_orcid_max_results = 10

# Funktionsdefinitioner först
def initialize_session_state():
    """Initialisera sessionsvariabler för att komma ihåg tillstånd mellan Streamlit-omritningar."""
    if 'selected_researcher_id' not in st.session_state:
        st.session_state['selected_researcher_id'] = None

    if 'pubmed_results' not in st.session_state:
        st.session_state['pubmed_results'] = []

    if 'current_view' not in st.session_state:
        st.session_state['current_view'] = "search"

    if 'search_history' not in st.session_state:
        st.session_state['search_history'] = []
        
    if 'last_orcid_search' not in st.session_state:
        st.session_state.last_orcid_search = ""
        st.session_state.last_orcid_max_results = 10

def show_database_statistics():
    """Visa statistik om databasen i sidomenyn."""
    try:
        # Räkna antal forskare i permanent databas
        perm_count_query = "SELECT COUNT(*) as antal FROM forskare_permanent"
        try:
            perm_count = pd.read_sql(perm_count_query, permanent_engine).iloc[0]['antal']
        except:
            perm_count = 0
            
        # Räkna antal forskare i arbetsyta
        staging_count_query = "SELECT COUNT(*) as antal FROM forskare_cleanup"
        try:
            staging_count = pd.read_sql(staging_count_query, staging_engine).iloc[0]['antal']
        except:
            staging_count = 0
            
        # Visa statistik
        st.markdown("### Statistik")
        st.markdown(f"**Forskare i databasen:** {perm_count}")
        st.markdown(f"**Forskare i arbetsyta:** {staging_count}")
        
        # Visa ORCID-statistik
        orcid_query = """
        SELECT COUNT(*) as antal FROM forskare_permanent 
        WHERE orcid IS NOT NULL AND orcid != ''
        """
        try:
            orcid_count = pd.read_sql(orcid_query, permanent_engine).iloc[0]['antal']
            orcid_percent = (orcid_count / perm_count * 100) if perm_count > 0 else 0
            st.markdown(f"**Med ORCID:** {orcid_count} ({orcid_percent:.1f}%)")
        except:
            st.markdown("**Med ORCID:** 0 (0%)")
            
    except Exception as e:
        st.error(f"Fel vid visning av statistik: {str(e)}")

def show_recent_searches():
    """Visa de senaste sökningarna och gör dem klickbara."""
    if not st.session_state['search_history']:
        st.info("Du har inte utfört några sökningar än.")
    else:
        for idx, search in enumerate(st.session_state['search_history'][:10]):  # Visa bara de 10 senaste
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"🔍 {search}")
            with col2:
                if st.button("Sök", key=f"search_again_{idx}"):
                    # Sätt söktermen i session state och navigera till söksidan
                    st.session_state['current_search_term'] = search
                    st.rerun()

def show_recently_added_researchers():
    """Visa de senast tillagda forskarna från permanenta databasen."""
    try:
        # Hämta de 10 senast tillagda forskarna
        recent_query = """
        SELECT * FROM forskare_permanent 
        ORDER BY created_date DESC 
        LIMIT 10
        """
        try:
            recent_df = pd.read_sql(recent_query, permanent_engine)
            
            if recent_df.empty:
                st.info("Inga forskare i databasen ännu.")
            else:
                for i, row in recent_df.iterrows():
                    col1, col2 = st.columns([4, 1])
                    
                    with col1:
                        # Säkerställ att vi hanterar tomma namn
                        fname = row['namn'] if pd.notna(row['namn']) else ''
                        lname = row['efternamn'] if pd.notna(row['efternamn']) else ''
                        institution = row['institution'] if pd.notna(row['institution']) else 'Okänd institution'
                        full_name = f"{fname} {lname}".strip()
                        if not full_name:
                            full_name = "Okänt namn"
                        st.write(f"**{full_name}** ({institution})")
                    
                    with col2:
                        if st.button("Visa", key=f"view_researcher_{row['id']}"):
                            # Sätt forskaren som vald i session state och visa detaljvyn
                            st.session_state['selected_researcher_id'] = row['id']
                            st.session_state['current_view'] = "researcher_detail"
                            st.rerun()
                            
        except Exception as e:
            st.info(f"Kunde inte hämta senaste forskare: Tabellen finns troligen inte än.")
    except Exception as e:
        st.error(f"Fel vid visning av senaste forskare: {str(e)}")

def process_excel_file(uploaded_file):
    """Processera en uppladdad Excel-fil och extrahera forskare."""
    try:
        # Läs Excel-filen
        df = pd.read_excel(uploaded_file)
        
        # Skapa tomma listor för att lagra resultat
        processed_data = []
        skipped_records = []
        
        # Identifiera kolumnnamn i filen
        column_mappings = {
            'namn': ['namn', 'förnamn', 'fornamn', 'name', 'given_name', 'first_name', 'firstname'],
            'efternamn': ['efternamn', 'lastname', 'last_name', 'family_name', 'surname'],
            'institution': ['institution', 'affiliation', 'organisation', 'organization'],
            'orcid': ['orcid', 'orcid_id', 'orcid-id'],
            'email': ['email', 'e-post', 'epost', 'e-mail', 'mail'],
            'pmid': ['pmid', 'pubmed', 'pubmed_id']
        }
        
        # Mappa kolumner från Excel-filen till våra standardkolumner
        actual_columns = {}
        for our_col, possible_cols in column_mappings.items():
            found = False
            for possible_col in possible_cols:
                if possible_col in df.columns:
                    actual_columns[our_col] = possible_col
                    found = True
                    break
            if not found:
                # Om viktig kolumn saknas, rapportera det
                if our_col in ['namn', 'efternamn']:
                    return False, f"Kolumn för {our_col} hittades inte i Excel-filen", []
        
        # Loop genom varje rad i Excel-filen
        for index, row in df.iterrows():
            # Skapa en dictionary för forskaren
            researcher = {}
            
            # Kopiera data från Excel enligt mappningen
            for our_col, excel_col in actual_columns.items():
                if pd.notna(row.get(excel_col)):
                    researcher[our_col] = str(row.get(excel_col))
                else:
                    researcher[our_col] = ""
            
            # Kontrollera att nödvändiga fält finns
            if researcher.get('namn') and researcher.get('efternamn'):
                # Om ORCID saknas men vi har namn och institution, försök hitta ORCID
                if not researcher.get('orcid') and researcher.get('institution'):
                    orcid = search_orcid(researcher['namn'], researcher['efternamn'], researcher['institution'])
                    if orcid:
                        researcher['orcid'] = orcid
                
                processed_data.append(researcher)
            else:
                skipped_records.append(f"Rad {index+2}: Saknar namn eller efternamn")
        
        # Skapa meddelande
        if skipped_records:
            message = f"Importerade {len(processed_data)} forskare, hoppade över {len(skipped_records)} rader"
        else:
            message = f"Importerade {len(processed_data)} forskare"
            
        return True, message, processed_data
    
    except Exception as e:
        return False, f"Ett fel uppstod vid bearbetning av Excel-filen: {str(e)}", []

def search_orcid(firstname, lastname, institution):
    """Sök efter ORCID för en forskare baserat på namn och institution."""
    try:
        # Använd OrcidClient för att söka efter forskaren
        query = f"{firstname} {lastname} {institution}"
        researchers = orcid_client.search_researchers(query, max_results=1)
        
        if researchers and len(researchers) > 0:
            return researchers[0].get('orcid_id', '')
        
        return ""
    
    except Exception as e:
        st.warning(f"Kunde inte söka efter ORCID: {str(e)}")
        return ""

def save_to_database(researchers, engine=None, table="forskare_cleanup", permanent=False):
    """Spara forskare till databasen."""
    try:
        # Konvertera till DataFrame
        df = pd.DataFrame(researchers)
        
        if permanent:
            # Spara till permanent databas
            permanent_db.store_dataframe(df, table, source="app_import")
        else:
            # Spara direkt till databasen så att det hamnar i rätt tabell
            # Ändrat från staging_db.store_dataframe(df, table, schema_name="forskare")
            # som skapade fel med att tabellnamn och schema inte matchade
            df.to_sql(table, staging_engine, if_exists='append', index=False)
            st.success(f"Sparat {len(df)} forskare i arbetsytan")
        
        return True
    except Exception as e:
        st.error(f"Fel vid spara till databas: {str(e)}")
        return False

def search_orcid_researchers(search_term, max_results=10):
    """Sök efter forskare i ORCID API och returnera grundläggande information."""
    try:
        st.info(f"Söker efter forskare med term: '{search_term}'")
        
        # Använd OrcidClient istället för direkt API-anrop
        researchers = orcid_client.search_researchers(search_term, max_results)
        
        if not researchers:
            st.info("Inga forskare hittades")
            return []
        
        # Visa debug-info om resultatet om det finns men formatet är oväntat
        if researchers and not isinstance(researchers, list):
            st.warning(f"Oväntat format på sökresultatet: {type(researchers)}")
            st.write(researchers)
            return []
        
        st.success(f"Hittade {len(researchers)} forskare i ORCID")
                    
        # Anpassa formatet av resultatet för att matcha det som förväntas av resten av applikationen
        formatted_researchers = []
        for researcher in researchers:
            # Robust extrahering av identifierare
            orcid_id = researcher.get('orcid_id', researcher.get('orcid', ''))
            
            # Extrahera namn på flera möjliga sätt
            given_name = researcher.get('given_name', '')
            family_name = researcher.get('family_name', '')
            
            # Om given_name och family_name saknas, försök dela upp det fullständiga namnet
            if (not given_name or not family_name) and 'name' in researcher:
                full_name = researcher.get('name', '')
                
                # Dela upp namnet om det innehåller mellanslag
                name_parts = full_name.split(' ', 1)
                if len(name_parts) > 1:
                    if not given_name:  # Använd bara om given_name inte redan finns
                        given_name = name_parts[0]
                    if not family_name:  # Använd bara om family_name inte redan finns
                        family_name = name_parts[1]
                else:
                    if not given_name:  # Använd bara om given_name inte redan finns
                        given_name = full_name
            
            # Om vi fortfarande saknar delar av namnet men har display-name
            if (not given_name or not family_name) and 'display-name' in researcher:
                display_name = researcher.get('display-name', '')
                
                # Dela upp namnet om det innehåller mellanslag
                name_parts = display_name.split(' ', 1)
                if len(name_parts) > 1:
                    if not given_name:  # Använd bara om given_name inte redan finns
                        given_name = name_parts[0]
                    if not family_name:  # Använd bara om family_name inte redan finns
                        family_name = name_parts[1]
                else:
                    if not given_name:  # Använd bara om given_name inte redan finns
                        given_name = display_name
            
            # Extrahera institution på flera möjliga sätt
            institution = ""
            if 'institution' in researcher:
                institution = researcher['institution']
            elif 'affiliation' in researcher:
                institution = researcher['affiliation']
            elif 'employments' in researcher and researcher['employments']:
                if isinstance(researcher['employments'], list) and len(researcher['employments']) > 0:
                    institution = researcher['employments'][0].get('organization', '')
                    
            # Säkerställ att vi har något att visa
            if not given_name and not family_name:
                # Försök med display-name direkt
                display_name = researcher.get('display-name', '')
                if display_name:
                    name_parts = display_name.split(' ', 1)
                    if len(name_parts) > 1:
                        given_name = name_parts[0]
                        family_name = name_parts[1]
                    else:
                        given_name = display_name
                        family_name = ""
                else:
                    given_name = "Okänt"
                    family_name = "namn"
            
            researcher_data = {
                'orcid': orcid_id,
                'namn': given_name,
                'efternamn': family_name,
                'institution': institution
            }
            
            formatted_researchers.append(researcher_data)
        
        # Sortera resultatet efter efternamn
        formatted_researchers.sort(key=lambda x: x['efternamn'])
                
        return formatted_researchers
        
    except Exception as e:
        st.error(f"Ett fel uppstod vid sökning i ORCID: {str(e)}")
        import traceback
        st.error(traceback.format_exc())  # Visa fullständigt fel för felsökning
        return []

def get_basic_researcher_info(orcid_id):
    """Hämta grundläggande information om en forskare från ORCID API (bara namn, institution, ORCID)."""
    try:
        # Använd OrcidClient för att hämta forskarinformation
        researcher = orcid_client.get_researcher_info(orcid_id)
        
        if not researcher:
            return None
            
        # Extrahera institution på flera möjliga sätt
        institution = ""
        if 'institution' in researcher:
            institution = researcher['institution']
        elif 'employments' in researcher and researcher['employments']:
            if isinstance(researcher['employments'], list) and len(researcher['employments']) > 0:
                institution = researcher['employments'][0].get('organization', '')
            
        # Formatera om data för att matcha förväntat format i applikationen
        formatted_researcher = {
            'orcid': orcid_id,
            'namn': researcher.get('given_name', ''),
            'efternamn': researcher.get('family_name', ''),
            'institution': institution
        }
        
        return formatted_researcher
        
    except Exception as e:
        st.warning(f"Kunde inte hämta grundläggande info för {orcid_id}: {str(e)}")
        import traceback
        st.warning(traceback.format_exc())  # Visa fullständigt fel för felsökning
        return None

def _format_date(date_obj):
    """Formatera ett datumsobjekt från ORCID API till en läsbar sträng."""
    # Hantera None-värden direkt
    if date_obj is None:
        return None
        
    # Hantera om date_obj är en dict vs. en sträng
    if isinstance(date_obj, dict):
        # Försök extrahera år, månad, dag i ordning
        year = date_obj.get('year', {}) if isinstance(date_obj.get('year', {}), dict) else date_obj.get('year')
        year = year.get('value') if isinstance(year, dict) else year
        
        month = date_obj.get('month', {}) if isinstance(date_obj.get('month', {}), dict) else date_obj.get('month')
        month = month.get('value') if isinstance(month, dict) else month
        
        day = date_obj.get('day', {}) if isinstance(date_obj.get('day', {}), dict) else date_obj.get('day')
        day = day.get('value') if isinstance(day, dict) else day
        
        # Skapa datum utifrån de komponenter som finns
        date_str = ""
        if year:
            date_str += str(year)
            if month:
                date_str += f"-{month}"
                if day:
                    date_str += f"-{day}"
        return date_str if date_str else None
        
    elif isinstance(date_obj, str):
        # Om det redan är en sträng, returnera den direkt
        return date_obj
        
    return None

def fetch_complete_orcid_data(orcid: str) -> dict:
    """
    Hämtar komplett data från ORCID API för det angivna ORCID-numret.
    Inkluderar fullständigt namn, alternativa namn, kontaktinfo, biografi, anställningar,
    utbildning, publikationer, finansiering, affilieringar och andra identifierare.
    """
    try:
        # Använd OrcidClient för att hämta fullständig data
        researcher_data = orcid_client.get_researcher_info(orcid, include_details=True)
        
        if not researcher_data:
            raise Exception(f"Kunde inte hämta data för ORCID {orcid}")
        
        # Formatera om data till det format som används i applikationen
        # OrcidClient.get_researcher_info med include_details=True ger redan ett liknande format
        # men vi kan behöva anpassa det ytterligare för vår applikation
        
        person_data = {
            "orcid": orcid,
            "name": {
                "full_name": researcher_data.get("name", ""),
                "given_name": researcher_data.get("given_name", ""),
                "family_name": researcher_data.get("family_name", ""),
                "other_names": researcher_data.get("other_names", [])
            },
            "contact": researcher_data.get("contact", {}),
            "biography": researcher_data.get("biography", ""),
            "keywords": researcher_data.get("keywords", []),
            "employments": researcher_data.get("employments", []),
            "educations": researcher_data.get("educations", []),
            "works": researcher_data.get("works", []),
            "fundings": researcher_data.get("fundings", []),
            "services": researcher_data.get("services", []),
            "external_identifiers": researcher_data.get("external_identifiers", []),
            "last_updated": datetime.datetime.now().isoformat()
        }
        
        return person_data
    except Exception as e:
        st.error(f"Fel vid hämtning från ORCID API: {str(e)}")
        raise

def save_complete_orcid_profile(orcid, engine=None, permanent_db=True):
    """Hämta och spara komplett ORCID-profil för en forskare."""
    try:
        st.info(f"Hämtar data för ORCID: {orcid}...")
        
        # Kontrollera om vi är i debug-läge och använd testdata i så fall
        if hasattr(orcid_client, 'debug_mode') and orcid_client.debug_mode:
            st.warning("Debug-läge aktiverat. Returnerar testdata istället för att anropa ORCID API.")
            # Returnera en dummy-profil för testning
            person_data = {
                "orcid": orcid,
                "given_name": "Test",
                "family_name": "Forskare",
                "biography": "Detta är en testprofil som skapats i debug-läge",
                "institution": "Testinstitution",
                "works": [],
                "employments": [{"organization": "Testinstitution"}],
                "contact": {"emails": [{"email": "test@example.com"}]},
                "educations": [],
                "fundings": [],
                "keywords": ["test", "debug"],
                "external_identifiers": []
            }
        else:
            # Hämta detaljerad data med OrcidClient
            person_data = orcid_client.get_researcher_info(orcid, include_details=True)
            
            if not person_data:
                error_msg = f"Kunde inte hämta data för ORCID {orcid}"
                st.error(error_msg)
                return False, None
            
            # Logga nycklarna som vi fått för felsökning
            st.info(f"Fick data med nycklarna: {', '.join(person_data.keys())}")
        
        # Säkerställ att vi har ORCID-ID i data
        person_data["orcid"] = orcid
        
        # Välj rätt databas och tabell baserat på om det är permanent eller temporär
        if permanent_db:
            db_engine = permanent_engine
            profile_table = "forskare_profiler"
        else:
            db_engine = staging_engine
            profile_table = "forskare_temp_profiler"
        
        # Skapa tabellen om den inte finns
        with db_engine.connect() as conn:
            conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {profile_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                orcid TEXT UNIQUE,
                profile_data TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """))
            
            # Konvertera person_data till JSON för lagring
            profile_json = json.dumps(person_data)
            
            # Spara till databasen med UPSERT-logik
            conn.execute(text(f"""
            INSERT INTO {profile_table} (orcid, profile_data, last_updated)
            VALUES (:orcid, :profile_data, CURRENT_TIMESTAMP)
            ON CONFLICT(orcid) DO UPDATE SET
            profile_data = :profile_data, last_updated = CURRENT_TIMESTAMP
            """), {'orcid': orcid, 'profile_data': profile_json})
            
            conn.commit()
        
        st.success(f"Profil för {person_data.get('given_name', '')} {person_data.get('family_name', '')} sparad!")
        return True, person_data
    
    except Exception as e:
        st.error(f"Fel vid hämtning eller lagring av ORCID-profil: {str(e)}")
        import traceback
        st.error(traceback.format_exc())
        return False, None

def move_to_permanent_db(researcher_id, engine):
    """Flytta en forskare från arbetsytan till permanenta databasen."""
    try:
        # Försök hämta forskaren från arbetsytan med rowid
        try:
            query = f"SELECT rowid, * FROM forskare_cleanup WHERE rowid = {researcher_id}"
            researcher_df = pd.read_sql(query, engine)
        except Exception as e:
            return False, f"Kunde inte hämta forskare med rowid {researcher_id}: {str(e)}"
        
        if len(researcher_df) == 0:
            return False, "Forskare hittades inte i arbetsytan"
        
        # Kontrollera om forskaren redan finns i permanenta databasen, men bara om ORCID finns
        orcid = researcher_df.iloc[0]['orcid'] if pd.notna(researcher_df.iloc[0]['orcid']) else None
        if orcid:
            try:
                check_query = f"SELECT * FROM forskare_permanent WHERE orcid = '{orcid}'"
                existing = pd.read_sql(check_query, permanent_engine)
                if len(existing) > 0:
                    return False, f"Forskare med ORCID {orcid} finns redan i permanenta databasen"
            except Exception as e:
                # Om tabellen inte finns än, ignorera felet och fortsätt
                pass
        
        # Även om ORCID saknas, kontrollera om namn+efternamn+institution matchar
        namn = researcher_df.iloc[0]['namn']
        efternamn = researcher_df.iloc[0]['efternamn']
        institution = researcher_df.iloc[0]['institution'] if pd.notna(researcher_df.iloc[0]['institution']) else ""
        
        if namn and efternamn:
            try:
                check_query = f"""
                SELECT * FROM forskare_permanent 
                WHERE namn = '{namn}' 
                AND efternamn = '{efternamn}'
                AND institution = '{institution}'
                """
                existing = pd.read_sql(check_query, permanent_engine)
                if len(existing) > 0:
                    return False, f"Forskare med namn {namn} {efternamn} vid {institution} finns redan i permanenta databasen"
            except Exception as e:
                # Om tabellen inte finns än, ignorera felet och fortsätt
                pass
        
        # Skapa permanenta forskartabellen om den inte finns
        with permanent_engine.connect() as conn:
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS forskare_permanent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namn TEXT,
                efternamn TEXT,
                orcid TEXT,
                institution TEXT,
                email TEXT,
                notes TEXT,
                pmid TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """))
        
        # Spara forskaren i permanenta databasen
        researcher_data = researcher_df.to_dict('records')[0]
        # Ta bort rowid och id för att låta databasen generera ett nytt id
        if 'rowid' in researcher_data:
            del researcher_data['rowid']
        if 'id' in researcher_data:
            del researcher_data['id']
            
        # Lägg till i permanenta databasen via pandas
        pd.DataFrame([researcher_data]).to_sql("forskare_permanent", permanent_engine, if_exists="append", index=False)
        
        # Registrera i permanent_db dataset-tabell
        dataset_info = {
            'name': 'forskare_permanent',
            'source': 'staging_db',
            'record_count': 1
        }
        
        # Om forskaren har en ORCID, försök hämta komplett profil till permanenta databasen
        if orcid:
            try:
                # Kontrollera om det finns en fullständig profil i arbetsytan
                temp_profile_query = f"SELECT * FROM forskare_temp_profiler WHERE orcid = '{orcid}'"
                temp_profile_exists = False
                temp_profile_data = None
                
                try:
                    temp_profile_df = pd.read_sql(temp_profile_query, staging_engine)
                    if not temp_profile_df.empty:
                        temp_profile_exists = True
                        temp_profile_data = json.loads(temp_profile_df.iloc[0]['profile_data'])
                except Exception as e:
                    # Ignorera om tabellen inte finns
                    pass
                
                if temp_profile_exists and temp_profile_data:
                    # Om profilen finns i arbetsytan, kopiera den till permanenta
                    with permanent_engine.connect() as conn:
                        conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS forskare_profiler (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            orcid TEXT UNIQUE,
                            profile_data TEXT,
                            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                        """))
                    
                    # Konvertera till JSON och spara
                    profile_json = json.dumps(temp_profile_data)
                    profile_df = pd.DataFrame([{'orcid': orcid, 'profile_data': profile_json}])
                    profile_df.to_sql("forskare_profiler", permanent_engine, if_exists="append", index=False)
                else:
                    # Annars, hämta profilen direkt från ORCID API till permanenta databasen
                    success, profile_data = save_complete_orcid_profile(orcid, permanent_engine, permanent_db=True)
                    if not success:
                        st.warning("Kunde inte hämta komplett ORCID-profil, men forskaren har flyttats")
                
                # Registrera ORCID-koppling i permanent_db
                permanent_db.register_orcid_mapping(
                    dataset_id=1,  # Vi använder ID 1 för forskare_permanent tabellen
                    record_id=f"{namn} {efternamn}",
                    orcid=orcid,
                    confidence=1.0  # Hög konfidens eftersom användaren manuellt flyttar
                )
            except Exception as orcid_error:
                st.warning(f"Fel vid hantering av ORCID-profil: {str(orcid_error)}, men forskaren har flyttats")
        
        # Ta bort från arbetsytan efter att ha flyttat
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM forskare_cleanup WHERE rowid = {researcher_id}"))
            conn.commit()
        
        return True, "Forskare flyttad till permanenta databasen"
    
    except Exception as e:
        return False, f"Fel vid flytt: {str(e)}"

def validate_orcid(orcid):
    """Validera ORCID-format."""
    import re
    pattern = r'^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$'
    return bool(re.match(pattern, orcid))

def fetch_and_update_orcid_profile(researcher_id, orcid):
    """Hämta och uppdatera ORCID-profil för en forskare."""
    try:
        if not validate_orcid(orcid):
            st.error(f"Ogiltigt ORCID-format: {orcid}")
            return False, None
            
        st.info(f"Hämtar ORCID-profil för {orcid}...")
        
        # Hämta detaljerad ORCID-data med statusindikator
        with st.spinner("Hämtar detaljerad forskardata från ORCID..."):
            success, profile_data = save_complete_orcid_profile(orcid, permanent_engine, permanent_db=True)
        
        if not success:
            st.error("Kunde inte hämta ORCID-profil")
            return False, None
            
        st.success(f"ORCID-profil hämtad för {profile_data.get('given_name', '')} {profile_data.get('family_name', '')}")
        
        # Uppdatera forskaren i den permanenta databasen med ny information
        if researcher_id:
            email = ""
            contact_info = profile_data.get('contact', {}) or {}
            emails_list = contact_info.get('emails', []) or []
            if emails_list and len(emails_list) > 0 and isinstance(emails_list[0], dict):
                email = emails_list[0].get('email', '')
                
            # Hämta biografi om den finns
            biography = profile_data.get('biography', '') or ''
            biography = biography[:500]  # Begränsa till 500 tecken
            
            # Extrahera institution på flera möjliga sätt
            institution = ""
            if 'institution' in profile_data:
                institution = profile_data['institution']
            elif 'employments' in profile_data and profile_data['employments']:
                if isinstance(profile_data['employments'], list) and len(profile_data['employments']) > 0:
                    institution = profile_data['employments'][0].get('organization', '')
            
            # Uppdatera existerande forskare med ny information
            with permanent_engine.connect() as conn:
                conn.execute(text("""
                UPDATE forskare_permanent
                SET email = :email, 
                    institution = :institution, 
                    notes = :notes
                WHERE id = :id
                """), {
                    'email': email,
                    'institution': institution,
                    'notes': biography,
                    'id': researcher_id
                })
            
            st.success(f"Forskarprofil uppdaterad med information från ORCID")
            
        return success, profile_data
    except Exception as e:
        st.error(f"Fel vid hämtning av ORCID-profil: {str(e)}")
        import traceback
        st.error(traceback.format_exc())
        return False, None

def search_pubmed(search_term=None, max_results=10, researcher=None):
    """
    Sök efter publikationer på PubMed baserat på sökterm eller forskaruppgifter.
    
    Args:
        search_term: Direkt sökterm för PubMed
        max_results: Max antal resultat att returnera
        researcher: Forskare-objekt med namn, efternamn, och institution
    
    Returns:
        Lista med formaterade publikationer
    """
    try:
        # Om en direkt sökterm skickats, använd den
        if search_term and not researcher:
            # Direkt sökning med användarens term
            pass
        # Annars, bygg söktermen från forskare-objektet
        elif researcher:
            firstname = researcher.get('namn', '')
            lastname = researcher.get('efternamn', '')
            institution = researcher.get('institution', '')
            
            search_term = f"{lastname} {firstname[0] if firstname else ''}"
            if institution:
                search_term += f" AND {institution}[Affiliation]"
        else:
            st.warning("Ingen sökterm eller forskardata angiven")
            return []
        
        st.info(f"Söker efter publikationer med term: {search_term}")
        
        # Använd PubMedCollector för att söka
        articles = pubmed_collector.search_articles(search_term, max_results=max_results)
        
        # Om inga resultat, returnera tom lista
        if not articles:
            st.info("Inga publikationer hittades")
            return []
        
        # Formatera publikationerna
        publications = []
        for article in articles:
            pub = {
                "title": article.get("title", "Ingen titel"),
                "authors": article.get("authors", "Okänd"),
                "journal": article.get("journal", "Okänd journal"),
                "publication_date": article.get("publication_date", "Okänt datum"),
                "pmid": article.get("pmid", ""),
                "abstract": article.get("abstract", "Inget abstract tillgängligt"),
            }
            publications.append(pub)
        
        return publications
    
    except Exception as e:
        st.error(f"Fel vid sökning i PubMed: {str(e)}")
        import traceback
        st.error(traceback.format_exc())
        return []

def perform_researcher_search(search_term):
    """Utför sökning efter forskare och visar resultaten"""
    query = f"""
    SELECT * FROM forskare_permanent
    WHERE namn LIKE '%{search_term}%'
    OR efternamn LIKE '%{search_term}%'
    OR orcid LIKE '%{search_term}%'
    OR institution LIKE '%{search_term}%'
    """
    try:
        df = pd.read_sql(query, permanent_engine)
        st.session_state['last_search_results'] = df
        
        if not df.empty:
            st.success(f"Hittade {len(df)} forskare")
            display_researcher_list(df)
        else:
            st.info("Inga forskare matchade sökningen.")
    except Exception as e:
        st.error(f"Fel vid sökning: {str(e)}")

def display_researcher_list(df):
    """Visar en lista med forskare som användaren kan klicka på för att se detaljer"""
    if df.empty:
        st.info("Inga forskare att visa.")
        return
        
    # Skapa en tabell med forskare som kan klickas på
    for i, row in df.iterrows():
        col1, col2, col3, col4 = st.columns([1, 2, 1.5, 0.5])
        
        with col1:
            if pd.notna(row['orcid']):
                st.image("https://orcid.org/sites/default/files/images/orcid_16x16.png", width=16)
            else:
                st.write("👤")
                
        with col2:
            name = f"{row['namn']} {row['efternamn']}".strip()
            if not name:
                name = "Okänt namn"
            st.markdown(f"**{name}**")
            
        with col3:
            institution = row['institution'] if pd.notna(row['institution']) else "Okänd institution"
            st.write(institution)
            
        with col4:
            if st.button("Visa", key=f"show_{row['id']}"):
                st.session_state['selected_researcher_id'] = row['id']
                st.session_state['current_view'] = "researcher_detail"
                st.rerun()
        
        st.divider()

def show_researcher_detail_view():
    """Visar detaljerad vy för en utvald forskare"""
    # Lägg till tillbakaknapp
    if st.button("← Tillbaka till sökresultat"):
        st.session_state['current_view'] = "search"
        st.rerun()
    
    # Hämta forskaren från databasen
    researcher_id = st.session_state['selected_researcher_id']
    
    try:
        researcher_query = f"SELECT * FROM forskare_permanent WHERE id = {researcher_id}"
        researcher_df = pd.read_sql(researcher_query, permanent_engine)
        
        if researcher_df.empty:
            st.error("Forskaren kunde inte hittas i databasen")
            return
            
        researcher = researcher_df.iloc[0]
        
        # === ÖVRE DELEN MED BILD OCH GRUNDLÄGGANDE INFO ===
        col_image, col_info = st.columns([1, 3])
        
        with col_image:
            # Placeholder för forskarbild
            st.image("https://cdn.pixabay.com/photo/2015/10/05/22/37/blank-profile-picture-973460_960_720.png", width=150)
            
        with col_info:
            # Forskarens huvudinformation
            st.title(f"{researcher['namn']} {researcher['efternamn']}")
            
            # Visa flera rader med basinformation
            info_col1, info_col2 = st.columns(2)
            
            with info_col1:
                st.markdown(f"**Institution:** {researcher['institution'] if pd.notna(researcher['institution']) else 'Ej angiven'}")
                if pd.notna(researcher['email']):
                    st.markdown(f"**E-post:** {researcher['email']}")
            
            with info_col2:
                if pd.notna(researcher['orcid']):
                    st.markdown(f"**ORCID:** [{researcher['orcid']}](https://orcid.org/{researcher['orcid']})")
            
            # Lägg till knappar för datainhämtning
            button_col1, button_col2, button_col3, button_col4 = st.columns(4)
            with button_col1:
                # ORCID-uppdatering
                if st.button("📝 Uppdatera från ORCID", use_container_width=True):
                    if pd.notna(researcher['orcid']):
                        with st.spinner(f"Hämtar fullständig ORCID-profil..."):
                            success, profile_data = fetch_and_update_orcid_profile(researcher_id, researcher['orcid'])
                            if success:
                                st.success("ORCID-profil uppdaterad!")
                                st.rerun()
                    else:
                        st.warning("Forskaren har ingen ORCID-identifierare.")
            
            with button_col2:
                # PubMed-sökning
                if st.button("🔬 Sök i PubMed", use_container_width=True):
                    # Spara att vi ska visa PubMed-resultat
                    st.session_state['show_pubmed_search'] = True
                    st.session_state['show_google_scholar'] = False
                    st.rerun()
            
            with button_col3:
                # Google Scholar-sökning
                if st.button("🎓 Sök i Google Scholar", use_container_width=True):
                    # Spara att vi ska visa Google Scholar
                    st.session_state['show_google_scholar'] = True
                    st.session_state['show_pubmed_search'] = False
                    
                    # Utför sökningen direkt
                    full_name = f"{researcher['namn']} {researcher['efternamn']}"
                    orcid_val = researcher['orcid'] if pd.notna(researcher['orcid']) else None
                    
                    with st.spinner(f"Söker efter {full_name} på Google Scholar..."):
                        scholar_data = search_google_scholar(full_name, orcid=orcid_val)
                        st.session_state['scholar_data'] = scholar_data
                    
                    st.rerun()
            
            with button_col4:
                # Redigera forskare
                if st.button("✏️ Redigera forskare", use_container_width=True):
                    st.session_state['edit_researcher'] = True
                    st.session_state['edit_researcher_data'] = researcher.to_dict()
                    st.rerun()
        
        # === VISA GOOGLE SCHOLAR STATISTIK OM TILLGÄNGLIGT ===
        if 'scholar_data' in st.session_state and st.session_state['scholar_data'] and st.session_state['scholar_data']['profile_url']:
            scholar_data = st.session_state['scholar_data']
            
            st.subheader("📊 Statistik från Google Scholar")
            
            # Visa statistik i metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Citeringar", scholar_data['citations'])
            with col2:
                st.metric("H-index", scholar_data['h_index'])
            with col3:
                st.metric("i10-index", scholar_data['i10_index'])
            
            # Visa länk till Google Scholar-profilen
            st.markdown(f"[Visa fullständig profil på Google Scholar]({scholar_data['profile_url']})")
            
            # Visa forskningsintressen om de finns
            if scholar_data['interests']:
                st.markdown("**Forskningsintressen:**")
                interests_html = ""
                for interest in scholar_data['interests']:
                    interests_html += f"<span style='background-color: #f0f2f6; padding: 3px 8px; margin-right: 8px; border-radius: 10px;'>{interest}</span>"
                st.markdown(interests_html, unsafe_allow_html=True)
            
            # Visa medförfattare
            if scholar_data['coauthors']:
                st.markdown("### 👥 Medförfattare")
                
                # Visa i table_cols kolumner
                table_cols = 3
                rows = [scholar_data['coauthors'][i:i+table_cols] for i in range(0, len(scholar_data['coauthors']), table_cols)]
                
                for row in rows:
                    cols = st.columns(table_cols)
                    for i, coauthor in enumerate(row):
                        with cols[i]:
                            st.markdown(f"**[{coauthor['name']}]({coauthor['profile_url']})**")
        
        # === DETALJERAD INFORMATION - VISA OM DET FINNS PROFIL ===
        has_profile = False
        profile_data = {}
        
        if pd.notna(researcher['orcid']):
            # Försök hämta profilen från databasen
            try:
                profile_query = f"SELECT * FROM forskare_profiler WHERE orcid = '{researcher['orcid']}'"
                profile_df = pd.read_sql(profile_query, permanent_engine)
                
                if not profile_df.empty:
                    has_profile = True
                    profile_data = json.loads(profile_df.iloc[0]['profile_data'])
            except Exception as e:
                st.error(f"Kunde inte läsa profildata: {str(e)}")
        
        # === FORMULÄR FÖR ATT REDIGERA FORSKARE ===
        if 'edit_researcher' in st.session_state and st.session_state['edit_researcher']:
            st.subheader("✏️ Redigera forskare")
            
            with st.form("edit_researcher_form"):
                col1, col2 = st.columns(2)
                
                with col1:
                    edit_firstname = st.text_input("Förnamn", value=researcher['namn'])
                    edit_institution = st.text_input("Institution", value=researcher['institution'] if pd.notna(researcher['institution']) else "")
                
                with col2:
                    edit_lastname = st.text_input("Efternamn", value=researcher['efternamn'])
                    edit_email = st.text_input("E-post", value=researcher['email'] if pd.notna(researcher['email']) else "")
                
                edit_orcid = st.text_input("ORCID ID", value=researcher['orcid'] if pd.notna(researcher['orcid']) else "")
                edit_notes = st.text_area("Anteckningar", value=researcher['notes'] if pd.notna(researcher['notes']) else "")
                
                # Knapp för att ta bort forskare
                col1, col2 = st.columns(2)
                
                submit = st.form_submit_button("Spara ändringar")
                
                if submit:
                    try:
                        # Uppdatera i databasen
                        update_query = f"""
                        UPDATE forskare_permanent 
                        SET namn = '{edit_firstname}', 
                            efternamn = '{edit_lastname}', 
                            institution = '{edit_institution}', 
                            email = '{edit_email}', 
                            orcid = '{edit_orcid}', 
                            notes = '{edit_notes}'
                        WHERE id = {researcher_id}
                        """
                        
                        with permanent_engine.connect() as conn:
                            conn.execute(text(update_query))
                            conn.commit()
                        
                        st.success("Forskarinformation uppdaterad!")
                        st.session_state['edit_researcher'] = False
                        st.rerun()
                    except Exception as e:
                        st.error(f"Kunde inte uppdatera forskare: {str(e)}")
            
            # Knapp för att ta bort forskaren helt
            st.warning("Varning: Detta går inte att ångra!")
            if st.button("🗑️ Ta bort forskare permanent"):
                st.warning("Är du säker på att du vill ta bort denna forskare permanent?")
                
                confirm_col1, confirm_col2 = st.columns(2)
                with confirm_col1:
                    if st.button("✓ Ja, ta bort permanent"):
                        try:
                            # Ta bort från databasen
                            delete_query = f"DELETE FROM forskare_permanent WHERE id = {researcher_id}"
                            
                            with permanent_engine.connect() as conn:
                                conn.execute(text(delete_query))
                                conn.commit()
                            
                            # Ta också bort eventuell profildata
                            if pd.notna(researcher['orcid']):
                                delete_profile_query = f"DELETE FROM forskare_profiler WHERE orcid = '{researcher['orcid']}'"
                                with permanent_engine.connect() as conn:
                                    conn.execute(text(delete_profile_query))
                                    conn.commit()
                            
                            st.success("Forskaren har tagits bort från databasen.")
                            # Återgå till söksidan
                            st.session_state['current_view'] = "search"
                            st.rerun()
                        except Exception as e:
                            st.error(f"Kunde inte ta bort forskare: {str(e)}")
                
                with confirm_col2:
                    if st.button("✗ Avbryt borttagning"):
                        st.session_state['edit_researcher'] = False
                        st.rerun()
            
            # Knapp för att avbryta redigering
            if st.button("Avbryt redigering"):
                st.session_state['edit_researcher'] = False
                st.rerun()
        
        # === VISA PUBMED-SÖKRESULTAT OM DET BEHÖVS ===
        if 'show_pubmed_search' in st.session_state and st.session_state['show_pubmed_search']:
            st.subheader("Sök publikationer i PubMed")
            
            # Förbered sökterm baserat på forskarens information
            default_search = f"{researcher['efternamn']} {researcher['namn'][0] if pd.notna(researcher['namn']) and len(researcher['namn']) > 0 else ''}"
            if pd.notna(researcher['institution']):
                default_search += f" AND {researcher['institution']}[Affiliation]"
                
            col1, col2 = st.columns([3, 1])
            
            with col1:
                pubmed_query = st.text_input("Sökterm för PubMed", value=default_search)
            
            with col2:
                search_button = st.button("Sök publikationer", use_container_width=True)
            
            if search_button or ('pubmed_results' not in st.session_state):
                with st.spinner("Söker i PubMed..."):
                    # Använd den uppdaterade search_pubmed-funktionen
                    articles = search_pubmed(pubmed_query, max_results=20)
                    if articles:
                        st.session_state['pubmed_results'] = articles
                    else:
                        st.warning("Inga publikationer hittades")
                        if 'pubmed_results' in st.session_state:
                            del st.session_state['pubmed_results']
            
            # Visa sökresultaten om de finns
            if 'pubmed_results' in st.session_state and st.session_state['pubmed_results']:
                st.success(f"Hittade {len(st.session_state['pubmed_results'])} publikationer")
                
                # Konvertera till DataFrame för snyggare visning
                df = pd.DataFrame(st.session_state['pubmed_results'])
                
                # Visa enbart de viktigaste kolumnerna först
                if set(['title', 'authors', 'journal', 'publication_date', 'pmid']).issubset(df.columns):
                    display_df = df[['title', 'authors', 'journal', 'publication_date', 'pmid']]
                    display_df.columns = ['Titel', 'Författare', 'Journal', 'Publiceringsdatum', 'PMID']
                    st.dataframe(display_df, use_container_width=True)
                else:
                    st.dataframe(df, use_container_width=True)
                
                # Möjlighet att visa detaljer om en specifik publikation
                if len(st.session_state['pubmed_results']) > 0:
                    selected_title = st.selectbox("Välj publikation för att se detaljer:", 
                                                [pub['title'] for pub in st.session_state['pubmed_results']])
                    
                    if selected_title:
                        selected_pub = next((pub for pub in st.session_state['pubmed_results'] if pub['title'] == selected_title), None)
                        
                        if selected_pub:
                            st.markdown(f"### {selected_pub['title']}")
                            st.markdown(f"**Författare:** {selected_pub['authors']}")
                            st.markdown(f"**Journal:** {selected_pub['journal']}")
                            st.markdown(f"**Publiceringsdatum:** {selected_pub['publication_date']}")
                            st.markdown(f"**PMID:** [{selected_pub['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{selected_pub['pmid']}/)")
                            
                            if 'abstract' in selected_pub and selected_pub['abstract']:
                                st.markdown("#### Abstract")
                                st.markdown(selected_pub['abstract'])
            
            # Knapp för att stänga PubMed-resultat
            if st.button("Stäng PubMed-sökning"):
                if 'show_pubmed_search' in st.session_state:
                    del st.session_state['show_pubmed_search']
                if 'pubmed_results' in st.session_state:
                    del st.session_state['pubmed_results']
                st.rerun()
        
        # === VISA GOOGLE SCHOLAR SÖKRESULTAT OM DET BEHÖVS ===
        if 'show_google_scholar' in st.session_state and st.session_state['show_google_scholar']:
            st.subheader("Sök i Google Scholar")
            
            # Forskare namn för sökning
            full_name = f"{researcher['namn']} {researcher['efternamn']}".strip()
            
            # Skapa Google Scholar URL
            scholar_url = f"https://scholar.google.com/scholar?q=author:%22{full_name.replace(' ', '+')}%22"
            
            st.markdown(f"""
            ### Google Scholar sökning för {full_name}
            
            Google Scholar API är inte tillgänglig utan speciell åtkomst, men du kan besöka 
            Google Scholar direkt via länken nedan:
            
            [🔍 Öppna Google Scholar för {full_name}]({scholar_url})
            
            #### Tips för manuell sökning:
            - Använd `author:"Namn Efternamn"` för att söka efter specifika författare
            - Lägg till universitetet för att begränsa sökningen: `author:"Namn Efternamn" Stockholm University`
            - Använd citattecken för exakta fraser: `"machine learning"`
            """)
            
            # Visa exempel på söktermer för forskaren
            institution = researcher['institution'] if pd.notna(researcher['institution']) else ""
            if institution:
                st.markdown(f"""
                ### Söktermer att prova:
                ```
                author:"{full_name}" {institution}
                ```
                """)
            
            # Knapp för att stänga Scholar-resultat
            if st.button("Stäng Google Scholar-sökning"):
                if 'show_google_scholar' in st.session_state:
                    del st.session_state['show_google_scholar']
                st.rerun()
        
        # Visa resten av profilen
        if has_profile and profile_data.get('biography'):
            st.markdown("### Biografi")
            st.markdown(profile_data.get('biography', ''))
        
        # === VISA NYCKELORD OM DE FINNS ===
        if has_profile and 'keywords' in profile_data and profile_data['keywords']:
            st.markdown("### Nyckelord")
            
            keywords = profile_data['keywords']
            # Omforma nyckelord till en platt lista
            keywords_list = []
            
            if isinstance(keywords, list):
                for kw in keywords:
                    if isinstance(kw, dict) and 'keyword' in kw:
                        keywords_list.append(kw.get('keyword', ''))
                    elif isinstance(kw, str):
                        keywords_list.append(kw)
            
            if keywords_list:
                # Visa nyckelord som taggar
                for keyword in keywords_list:
                    st.markdown(f"<span style='background-color: #f0f2f6; padding: 5px 10px; margin: 5px; border-radius: 20px; display: inline-block;'>{keyword}</span>", unsafe_allow_html=True)
        
        # === PUBLIKATIONER ===
        st.markdown("### Publikationer")
        
        if has_profile and 'works' in profile_data and profile_data['works']:
            # Visa publikationer från ORCID
            works = profile_data['works']
            if isinstance(works, list) and len(works) > 0:
                for work in works:
                    with st.expander(work.get('title', 'Okänd titel')):
                        st.markdown(f"**Publikationstyp:** {work.get('type', 'Ej angiven')}")
                        st.markdown(f"**Journal:** {work.get('journal-title', 'Ej angiven')}")
                        
                        # Visa DOI om det finns
                        if 'external-ids' in work and isinstance(work['external-ids'], list):
                            for ext_id in work['external-ids']:
                                if ext_id.get('type') == 'doi':
                                    st.markdown(f"**DOI:** [{ext_id.get('value')}](https://doi.org/{ext_id.get('value')})")
                                elif ext_id.get('type') == 'pmid':
                                    st.markdown(f"**PMID:** [{ext_id.get('value')}](https://pubmed.ncbi.nlm.nih.gov/{ext_id.get('value')}/)")
                        
                        # Visa url om det finns
                        if 'url' in work and work['url']:
                            st.markdown(f"**URL:** [{work['url']}]({work['url']})")
            else:
                st.info("Inga publikationer hittades i ORCID-profilen.")
                # Lägg till knapp för att söka i PubMed
                if not ('show_pubmed_search' in st.session_state and st.session_state['show_pubmed_search']):
                    if st.button("🔬 Sök i PubMed för publikationer"):
                        st.session_state['show_pubmed_search'] = True
                        st.rerun()
        else:
            st.info("Inga publikationer tillgängliga från ORCID.")
            # Lägg till knapp för att söka i PubMed
            if not ('show_pubmed_search' in st.session_state and st.session_state['show_pubmed_search']):
                if st.button("🔬 Sök i PubMed för publikationer"):
                    st.session_state['show_pubmed_search'] = True
                    st.rerun()
        
        # === ANSTÄLLNINGAR ===
        st.markdown("### Anställningar")
        
        if has_profile and 'employments' in profile_data and profile_data['employments']:
            employments = profile_data['employments']
            if isinstance(employments, list) and len(employments) > 0:
                for employment in employments:
                    title = employment.get('role-title', 'Okänd titel')
                    org = employment.get('organization', 'Okänd organisation')
                    st.markdown(f"**{title}** vid **{org}**")
                    
                    # Visa start/slutdatum om de finns
                    start_date = _format_date(employment.get('start-date'))
                    end_date = _format_date(employment.get('end-date'))
                    
                    if start_date or end_date:
                        date_text = f"{start_date or '?'} – {end_date or 'nu'}"
                        st.markdown(f"*{date_text}*")
                    
                    st.markdown("---")
            else:
                st.info("Inga anställningar hittades i ORCID-profilen.")
        else:
            st.info("Ingen anställningsinformation tillgänglig.")
        
        # === UTBILDNING ===
        st.markdown("### Utbildning")
        
        if has_profile and 'educations' in profile_data and profile_data['educations']:
            educations = profile_data['educations']
            if isinstance(educations, list) and len(educations) > 0:
                for education in educations:
                    title = education.get('role-title', 'Okänd utbildning')
                    org = education.get('organization', 'Okänd organisation')
                    st.markdown(f"**{title}** vid **{org}**")
                    
                    # Visa start/slutdatum om de finns
                    start_date = _format_date(education.get('start-date'))
                    end_date = _format_date(education.get('end-date'))
                    
                    if start_date or end_date:
                        date_text = f"{start_date or '?'} – {end_date or 'nu'}"
                        st.markdown(f"*{date_text}*")
                    
                    st.markdown("---")
            else:
                st.info("Ingen utbildningsinformation hittades i ORCID-profilen.")
        else:
            st.info("Ingen utbildningsinformation tillgänglig.")
        
        # === FINANSIERING ===
        st.markdown("### Finansiering")
        
        if has_profile and 'fundings' in profile_data and profile_data['fundings']:
            fundings = profile_data['fundings']
            if isinstance(fundings, list) and len(fundings) > 0:
                for funding in fundings:
                    title = funding.get('title', 'Okänd finansiering')
                    org = funding.get('organization', 'Okänd organisation')
                    st.markdown(f"**{title}** från **{org}**")
                    
                    # Visa start/slutdatum om de finns
                    start_date = _format_date(funding.get('start-date'))
                    end_date = _format_date(funding.get('end-date'))
                    
                    if start_date or end_date:
                        date_text = f"{start_date or '?'} – {end_date or 'nu'}"
                        st.markdown(f"*{date_text}*")
                    
                    st.markdown("---")
            else:
                st.info("Ingen finansieringsinformation hittades i ORCID-profilen.")
        else:
            st.info("Ingen finansieringsinformation tillgänglig.")
        
        # === EXTERNA IDENTIFIERARE ===
        st.markdown("### Externa identifierare")
        
        if has_profile and 'external_identifiers' in profile_data and profile_data['external_identifiers']:
            ext_ids = profile_data['external_identifiers']
            if isinstance(ext_ids, list) and len(ext_ids) > 0:
                for ext_id in ext_ids:
                    id_type = ext_id.get('type', 'Okänd typ')
                    id_value = ext_id.get('value', 'Okänt värde')
                    st.markdown(f"**{id_type}:** {id_value}")
            else:
                st.info("Inga externa identifierare hittades i ORCID-profilen.")
        else:
            st.info("Inga externa identifierare tillgängliga.")
    
    except Exception as e:
        st.error(f"Ett fel uppstod vid visning av forskarprofilen: {str(e)}")
        import traceback
        st.error(traceback.format_exc())

def show_staging_db_page():
    """Visa arbetsytan med forskare som ännu inte flyttats till permanenta databasen."""
    st.title("Arbetsyta")
    
    st.markdown("""
    Här kan du hantera forskare som du är intresserad av att arbeta med innan de flyttas till den permanenta databasen. 
    Du kan lägga till forskare här från ORCID, Excel eller manuellt för att samla och organisera data innan den läggs in i den permanenta databasen.
    """)
    
    # Skapa tabellen om den inte finns
    with staging_engine.connect() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS forskare_cleanup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namn TEXT,
            efternamn TEXT,
            orcid TEXT,
            institution TEXT,
            email TEXT,
            notes TEXT,
            pmid TEXT
        )
        """))
    
    # Testa databasens struktur för att avgöra om vi använder rowid eller id
    try:
        # Försök först med id-kolumnen
        query = "SELECT id, namn, efternamn, orcid, institution, email, notes FROM forskare_cleanup ORDER BY efternamn, namn"
        df = pd.read_sql(query, staging_engine)
    except Exception as e:
        st.info("Använder rowid istället för id")
        # Om det misslyckas, använd rowid istället
        query = "SELECT rowid as id, namn, efternamn, orcid, institution, email, notes FROM forskare_cleanup ORDER BY efternamn, namn"
        try:
            df = pd.read_sql(query, staging_engine)
        except Exception as e:
            st.error(f"Kunde inte hämta forskare: {str(e)}")
            # Fallback om något går fel
            df = pd.DataFrame()
    
    if not df.empty:
        st.write(f"**{len(df)} forskare i arbetsytan**")
        
        # Lägg till knappar för att hantera valda forskare
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("Flytta valda till permanenta databasen", use_container_width=True):
                # Hämta valda forskare
                selected_ids = []
                for key, value in st.session_state.items():
                    if key.startswith("select_") and value == True:
                        try:
                            researcher_id = int(key.split('_')[1])
                            selected_ids.append(researcher_id)
                        except (IndexError, ValueError):
                            pass
                
                if selected_ids:
                    success_count = 0
                    failure_messages = []
                    
                    for researcher_id in selected_ids:
                        success, message = move_to_permanent_db(researcher_id, staging_engine)
                        if success:
                            success_count += 1
                        else:
                            failure_messages.append(message)
                    
                    if success_count > 0:
                        st.success(f"{success_count} forskare flyttades till permanenta databasen")
                    
                    if failure_messages:
                        st.warning("Problem vid flytt av vissa forskare:")
                        for msg in failure_messages:
                            st.write(f"• {msg}")
                    
                    # Ladda om listan om något lyckades
                    if success_count > 0:
                        st.rerun()
                else:
                    st.warning("Inga forskare valda")
        
        with col2:
            if st.button("Ta bort valda forskare", use_container_width=True):
                # Hämta valda forskare
                selected_ids = []
                for key, value in st.session_state.items():
                    if key.startswith("select_") and value == True:
                        try:
                            researcher_id = int(key.split('_')[1])
                            selected_ids.append(researcher_id)
                        except (IndexError, ValueError):
                            pass
                
                if selected_ids:
                    # Visa bekräftelsedialog
                    st.warning(f"Vill du verkligen ta bort {len(selected_ids)} forskare från arbetsytan?")
                    
                    confirm_col1, confirm_col2 = st.columns(2)
                    with confirm_col1:
                        if st.button("✓ Ja, ta bort", key="confirm_delete"):
                            # Genomför borttagning
                            success_count = 0
                            error_count = 0
                            
                            for researcher_id in selected_ids:
                                try:
                                    with staging_engine.connect() as conn:
                                        # Använd explicit transaktion
                                        conn.execute(text("BEGIN TRANSACTION"))
                                        # Visa SQL för felsökning
                                        delete_sql = f"DELETE FROM forskare_cleanup WHERE rowid = {researcher_id}"
                                        st.info(f"Kör SQL: {delete_sql}")
                                        # Kör borttagningen
                                        result = conn.execute(text(delete_sql))
                                        # Kontrollera om något togs bort
                                        if result.rowcount > 0:
                                            success_count += 1
                                        conn.execute(text("COMMIT"))
                                except Exception as e:
                                    error_count += 1
                                    st.error(f"Fel vid borttagning av forskare {researcher_id}: {str(e)}")
                                    # Försök med alternativ metod
                                    try:
                                        with staging_engine.connect() as conn:
                                            conn.execute(text(f"DELETE FROM forskare_cleanup WHERE id = {researcher_id}"))
                                            conn.commit()
                                            success_count += 1
                                    except Exception as inner_e:
                                        st.error(f"Även alternativ metod misslyckades: {str(inner_e)}")
                            
                            if success_count > 0:
                                st.success(f"Tog bort {success_count} forskare")
                                # Rensa valda checkboxar
                                for key in list(st.session_state.keys()):
                                    if key.startswith("select_"):
                                        del st.session_state[key]
                                time.sleep(1)  # Kort paus så användaren hinner se meddelandet
                                st.rerun()
                            else:
                                st.error(f"Kunde inte ta bort några forskare. Kontakta administratören.")
                            
                        with confirm_col2:
                            if st.button("✗ Avbryt", key="cancel_delete"):
                                st.info("Borttagning avbruten")
                                st.rerun()
                else:
                    st.warning("Inga forskare valda")
        
        with col3:
            if st.button("Redigera vald forskare", use_container_width=True):
                # Hämta valda forskare
                selected_ids = []
                for key, value in st.session_state.items():
                    if key.startswith("select_") and value == True:
                        try:
                            researcher_id = int(key.split('_')[1])
                            selected_ids.append(researcher_id)
                        except (IndexError, ValueError):
                            pass
                
                if len(selected_ids) == 1:
                    # Lagra ID för den valda forskaren i session state
                    st.session_state.edit_researcher_id = selected_ids[0]
                    st.session_state.show_edit_form = True
                    st.rerun()
                elif len(selected_ids) > 1:
                    st.warning("Välj endast en forskare för redigering")
                else:
                    st.warning("Ingen forskare vald")
        
        # Lista forskare
        st.subheader("Forskare i arbetsytan")
        
        # Debug-information
        if st.checkbox("Visa debuginformation"):
            st.write("DataFrame med forskare:")
            st.write(df)
            
            # Visa alla kolumner i databasen
            st.write("Databasstruktur:")
            try:
                with staging_engine.connect() as conn:
                    # Hämta kolumninformation
                    result = conn.execute(text("PRAGMA table_info(forskare_cleanup)"))
                    columns = [dict(row) for row in result]
                    st.write(columns)
            except Exception as e:
                st.error(f"Kunde inte hämta databasstruktur: {str(e)}")
        
        for i, row in df.iterrows():
            # Använd row['id'] från SQL-frågan
            row_id = row['id']
            
            col1, col2, col3, col4 = st.columns([0.1, 0.3, 0.3, 0.3])
            
            with col1:
                checkbox_key = f"select_{row_id}"
                # Initiera session state om den inte finns innan vi visar checkboxen
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = False
                selected = st.checkbox("Välj", key=checkbox_key)
            
            with col2:
                # Säkerställ att vi hanterar tomma namn
                fname = row['namn'] if pd.notna(row['namn']) else ''
                lname = row['efternamn'] if pd.notna(row['efternamn']) else ''
                full_name = f"{fname} {lname}".strip()
                if not full_name:
                    full_name = "Okänt namn"
                st.write(f"**{full_name}**")
            
            with col3:
                # Visa institution om den finns, annars "Okänd"
                institution = row['institution'] if pd.notna(row['institution']) else 'Okänd institution'
                st.write(institution)
            
            with col4:
                orcid = row['orcid'] if pd.notna(row['orcid']) else ''
                if orcid:
                    st.write(f"[{orcid}](https://orcid.org/{orcid})")
                else:
                    st.write("Saknas ORCID")
        
        # Visa redigeringsformulär om en forskare är vald för redigering
        if 'show_edit_form' in st.session_state and st.session_state.show_edit_form:
            st.subheader("Redigera forskare")
            
            # Använd rowid för kompatibilitet
            query = f"SELECT rowid as id, * FROM forskare_cleanup WHERE rowid = {st.session_state.edit_researcher_id}"
            try:
                researcher_df = pd.read_sql(query, staging_engine)
            except Exception as e:
                st.error(f"Kunde inte hämta forskare: {str(e)}")
                researcher_df = pd.DataFrame()
            
            if not researcher_df.empty:
                selected_researcher = researcher_df.iloc[0]
                
                # Redigera forskarens data
                new_name = st.text_input("Förnamn", selected_researcher['namn'] if pd.notna(selected_researcher['namn']) else '')
                new_lastname = st.text_input("Efternamn", selected_researcher['efternamn'] if pd.notna(selected_researcher['efternamn']) else '')
                new_institution = st.text_input("Institution", selected_researcher['institution'] if pd.notna(selected_researcher['institution']) else '')
                new_email = st.text_input("Email", selected_researcher['email'] if pd.notna(selected_researcher['email']) else "")
                new_orcid = st.text_input("ORCID", selected_researcher['orcid'] if pd.notna(selected_researcher['orcid']) else "")
                new_notes = st.text_area("Noteringar", selected_researcher['notes'] if pd.notna(selected_researcher['notes']) else "")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Spara ändringar"):
                        with staging_engine.connect() as conn:
                            # Använd rowid för uppdatering
                            conn.execute(text(f"""
                            UPDATE forskare_cleanup 
                            SET namn = '{new_name}', 
                                efternamn = '{new_lastname}', 
                                institution = '{new_institution}', 
                                email = '{new_email}', 
                                orcid = '{new_orcid}',
                                notes = '{new_notes}' 
                            WHERE rowid = {st.session_state.edit_researcher_id}
                            """))
                            conn.commit()
                        st.success("Forskarens data har uppdaterats")
                        st.session_state.show_edit_form = False
                        st.rerun()
                
                with col2:
                    if st.button("Avbryt"):
                        st.session_state.show_edit_form = False
                        st.rerun()
            else:
                st.error("Kunde inte hitta forskaren. Försök igen.")
                st.session_state.show_edit_form = False
    else:
        st.info("Inga forskare i arbetsytan ännu.")
        st.write("Använd 'Lägg till forskare' för att lägga till forskare till arbetsytan.")

def show_add_researcher_page():
    """Sida för att lägga till forskare till arbetsytan"""
    st.title("Lägg till forskare")
    
    # Skapa flikar för olika sätt att lägga till forskare
    add_tabs = st.tabs(["Sök ORCID", "Direkt ORCID-ID", "Manuell inmatning", "Importera från Excel"])
    
    with add_tabs[0]:
        st.subheader("Sök och lägg till forskare från ORCID")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            orcid_search = st.text_input("Sök efter forskare (namn, institution, etc.)", key="orcid_search_term")
        
        with col2:
            search_button = st.button("Sök ORCID", use_container_width=True)
        
        if search_button and orcid_search:
            # Sök efter forskare i ORCID
            with st.spinner("Söker efter forskare..."):
                researchers = search_orcid_researchers(orcid_search, max_results=10)
                
                if researchers:
                    st.session_state['orcid_search_results'] = researchers
                    st.success(f"Hittade {len(researchers)} forskare")
                else:
                    st.warning("Inga forskare hittades")
        
        # Visa resultat om de finns
        if 'orcid_search_results' in st.session_state and st.session_state['orcid_search_results']:
            st.subheader("Sökresultat")
            
            for idx, researcher in enumerate(st.session_state['orcid_search_results']):
                col1, col2, col3, col4 = st.columns([0.1, 0.3, 0.3, 0.3])
                
                # Genrera ett unikt nyckelvärde för varje forskare
                checkbox_key = f"orcid_select_{researcher['orcid']}"
                
                with col1:
                    # Initiera session state för checkbox om den inte finns
                    if checkbox_key not in st.session_state:
                        st.session_state[checkbox_key] = False
                    
                    selected = st.checkbox("Välj", key=checkbox_key)
                
                with col2:
                    full_name = f"{researcher['namn']} {researcher['efternamn']}".strip()
                    if not full_name:
                        full_name = "Okänt namn"
                    st.write(f"**{full_name}**")
                
                with col3:
                    institution = researcher['institution'] if researcher['institution'] else "Okänd institution"
                    st.write(institution)
                
                with col4:
                    if researcher['orcid']:
                        st.write(f"[{researcher['orcid']}](https://orcid.org/{researcher['orcid']})")
                    else:
                        st.write("Saknas ORCID")
            
            # Knapp för att lägga till valda forskare
            if st.button("Lägg till valda forskare till arbetsytan"):
                selected_researchers = []
                for researcher in st.session_state['orcid_search_results']:
                    checkbox_key = f"orcid_select_{researcher['orcid']}"
                    if checkbox_key in st.session_state and st.session_state[checkbox_key]:
                        selected_researchers.append(researcher)
                
                if selected_researchers:
                    # Spara till databas
                    success = save_to_database(selected_researchers)
                    if success:
                        st.success(f"Lade till {len(selected_researchers)} forskare till arbetsytan")
                        # Rensa sökresultat och valda checkboxes
                        st.session_state.pop('orcid_search_results', None)
                        for key in list(st.session_state.keys()):
                            if key.startswith("orcid_select_"):
                                del st.session_state[key]
                        st.rerun()
                else:
                    st.warning("Inga forskare valda")
    
    with add_tabs[1]:
        st.subheader("Lägg till forskare via ORCID-ID")
        
        # För att stödja inklistring av flera ORCID:er
        orcid_input = st.text_area("Ange ORCID-ID (en per rad)", 
                               placeholder="Ex: 0000-0002-1234-5678\n0000-0003-8765-4321", 
                               help="Ange ett eller flera ORCID-ID:n, ett per rad")
        
        if st.button("Hämta forskare från ORCID-ID", use_container_width=True):
            if orcid_input:
                # Dela upp texten på rader
                orcid_list = [line.strip() for line in orcid_input.split('\n') if line.strip()]
                
                if orcid_list:
                    # Validera ORCID-format
                    valid_orcids = []
                    invalid_orcids = []
                    
                    for orcid in orcid_list:
                        if validate_orcid(orcid):
                            valid_orcids.append(orcid)
                        else:
                            invalid_orcids.append(orcid)
                    
                    if invalid_orcids:
                        st.warning(f"Följande ORCID-ID har ogiltigt format: {', '.join(invalid_orcids)}")
                    
                    if valid_orcids:
                        fetched_researchers = []
                        
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        for i, orcid in enumerate(valid_orcids):
                            status_text.text(f"Hämtar {i+1} av {len(valid_orcids)}: {orcid}")
                            researcher = fetch_researcher_by_orcid(orcid)
                            
                            if researcher:
                                fetched_researchers.append(researcher)
                            
                            # Uppdatera framstegsindikator
                            progress_bar.progress((i + 1) / len(valid_orcids))
                        
                        if fetched_researchers:
                            st.success(f"Hämtade information för {len(fetched_researchers)} forskare")
                            
                            # Visa de hämtade forskarna
                            for researcher in fetched_researchers:
                                col1, col2, col3 = st.columns([0.4, 0.3, 0.3])
                                
                                with col1:
                                    full_name = f"{researcher['namn']} {researcher['efternamn']}".strip()
                                    if not full_name:
                                        full_name = "Namn saknas"
                                    st.write(f"**{full_name}**")
                                
                                with col2:
                                    institution = researcher['institution'] if researcher['institution'] else "Institution saknas"
                                    st.write(institution)
                                
                                with col3:
                                    st.write(f"[{researcher['orcid']}](https://orcid.org/{researcher['orcid']})")
                            
                            # Spara till databasen
                            if st.button("Lägg till dessa forskare till arbetsytan", key="add_fetched_researchers"):
                                success = save_to_database(fetched_researchers)
                                if success:
                                    st.success(f"Lade till {len(fetched_researchers)} forskare till arbetsytan")
                                    st.rerun()
                        else:
                            st.warning("Kunde inte hämta information för någon forskare")
                else:
                    st.warning("Inga giltiga ORCID-ID angivna")
            else:
                st.warning("Ange minst ett ORCID-ID")
    
    with add_tabs[2]:
        st.subheader("Lägg till forskare manuellt")
        
        with st.form("add_researcher_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                firstname = st.text_input("Förnamn", key="add_firstname")
                institution = st.text_input("Institution", key="add_institution")
                email = st.text_input("E-post", key="add_email")
            
            with col2:
                lastname = st.text_input("Efternamn", key="add_lastname")
                orcid = st.text_input("ORCID ID (valfritt)", key="add_orcid")
                notes = st.text_area("Anteckningar", key="add_notes")
            
            submit = st.form_submit_button("Lägg till forskare")
            
            if submit:
                if firstname or lastname:  # Bara kräv minst ett av förnamn/efternamn
                    # Validera ORCID om det angivits
                    if orcid and not validate_orcid(orcid):
                        st.warning("Ogiltigt ORCID-format. Använd formatet: 0000-0000-0000-0000")
                    else:
                        # Skapa forskaren
                        researcher = {
                            'namn': firstname,
                            'efternamn': lastname,
                            'institution': institution,
                            'email': email,
                            'orcid': orcid,
                            'notes': notes
                        }
                        
                        # Spara till databas
                        success = save_to_database([researcher])
                        if success:
                            st.success("Forskare tillagd i arbetsytan")
                            # Återställ formuläret
                            for key in ["add_firstname", "add_lastname", "add_institution", 
                                       "add_email", "add_orcid", "add_notes"]:
                                st.session_state[key] = ""
                else:
                    st.warning("Ange minst förnamn eller efternamn")
    
    with add_tabs[3]:
        st.subheader("Importera forskare från Excel")
        
        st.write("""
        Ladda upp en Excel-fil med forskare. Filen bör ha följande kolumner:
        - namn (förnamn)
        - efternamn
        - institution (valfritt)
        - email (valfritt)
        - orcid (valfritt)
        - notes (valfritt)
        """)
        
        uploaded_file = st.file_uploader("Välj Excel-fil", type=["xlsx", "xls"])
        
        if uploaded_file is not None:
            # Hantera excelfilen
            process_excel_file(uploaded_file)

def search_google_scholar(researcher_name, max_attempts=3, orcid=None):
    """Sök efter en forskare på Google Scholar och försök extrahera profil information."""
    import requests
    from bs4 import BeautifulSoup
    import time
    import re
    
    try:
        st.info(f"Söker efter {researcher_name} på Google Scholar...")
        
        # Försök först med direkt sökning om ORCID finns
        if orcid and orcid.strip():
            # Även om Google Scholar inte använder ORCID direkt, kan vi prova att söka på det tillsammans med namnet
            direct_search_term = f"{researcher_name} {orcid}"
            st.info(f"Provar med direkt sökning: {direct_search_term}")
            
            direct_url = f"https://scholar.google.com/scholar?hl=sv&as_sdt=0%2C5&q={direct_search_term.replace(' ', '+')}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(direct_url, headers=headers)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Leta efter profillänk direkt i sökresultaten
                profile_links = soup.select('.gs_ai_name a')
                
                if profile_links:
                    # Använd första träffen
                    direct_profile_url = "https://scholar.google.com" + profile_links[0]['href']
                    st.success(f"Hittade profil direkt: {direct_profile_url}")
                    
                    # Besök profilen och fortsätt med resten av logiken
                    profile_response = requests.get(direct_profile_url, headers=headers)
                    
                    if profile_response.status_code == 200:
                        profile_soup = BeautifulSoup(profile_response.text, 'html.parser')
                        
                        # Extrahera information
                        citation_stats = profile_soup.select('.gsc_rsb_std')
                        
                        result = {
                            'name': profile_soup.select_one('#gsc_prf_in').text if profile_soup.select_one('#gsc_prf_in') else "",
                            'profile_url': direct_profile_url,
                            'citations': int(citation_stats[0].text) if len(citation_stats) > 0 else 0,
                            'h_index': int(citation_stats[2].text) if len(citation_stats) > 2 else 0,
                            'i10_index': int(citation_stats[4].text) if len(citation_stats) > 4 else 0,
                            'affiliation': profile_soup.select_one('.gsc_prf_il').text if profile_soup.select_one('.gsc_prf_il') else "",
                            'interests': [tag.text for tag in profile_soup.select('.gsc_prf_inta')],
                            'search_method': 'direct_orcid'
                        }
                        
                        # Leta efter medförfattare
                        coauthors = []
                        coauthor_elements = profile_soup.select('.gsc_rsb_aa')
                        for coauthor in coauthor_elements:
                            name_elem = coauthor.select_one('.gsc_rsb_a_desc a')
                            if name_elem:
                                coauthor_name = name_elem.text
                                coauthor_link = "https://scholar.google.com" + name_elem['href']
                                coauthors.append({
                                    'name': coauthor_name,
                                    'profile_url': coauthor_link
                                })
                        
                        result['coauthors'] = coauthors
                        
                        return result
        
        # Standardsökning om direktsökning misslyckas eller inte finns ORCID
        # Förbered sökterm
        search_term = researcher_name.replace(" ", "+")
        url = f"https://scholar.google.com/scholar?hl=sv&as_sdt=0%2C5&q={search_term}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Försök flera gånger om det behövs (för att hantera rate-limiting)
        for attempt in range(max_attempts):
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Leta efter profillänk i sökresultaten
                profile_links = soup.select('.gs_ai_name a')
                
                if profile_links:
                    # Ta första länken (mest relevant)
                    profile_url = "https://scholar.google.com" + profile_links[0]['href']
                    
                    # Besök profilsidan
                    profile_response = requests.get(profile_url, headers=headers)
                    
                    if profile_response.status_code == 200:
                        profile_soup = BeautifulSoup(profile_response.text, 'html.parser')
                        
                        # Extrahera information
                        citation_stats = profile_soup.select('.gsc_rsb_std')
                        
                        result = {
                            'name': profile_soup.select_one('#gsc_prf_in').text if profile_soup.select_one('#gsc_prf_in') else "",
                            'profile_url': profile_url,
                            'citations': int(citation_stats[0].text) if len(citation_stats) > 0 else 0,
                            'h_index': int(citation_stats[2].text) if len(citation_stats) > 2 else 0,
                            'i10_index': int(citation_stats[4].text) if len(citation_stats) > 4 else 0,
                            'affiliation': profile_soup.select_one('.gsc_prf_il').text if profile_soup.select_one('.gsc_prf_il') else "",
                            'interests': [tag.text for tag in profile_soup.select('.gsc_prf_inta')],
                            'search_method': 'name_search'
                        }
                        
                        # Leta efter medförfattare
                        coauthors = []
                        coauthor_elements = profile_soup.select('.gsc_rsb_aa')
                        for coauthor in coauthor_elements:
                            name_elem = coauthor.select_one('.gsc_rsb_a_desc a')
                            if name_elem:
                                coauthor_name = name_elem.text
                                coauthor_link = "https://scholar.google.com" + name_elem['href']
                                coauthors.append({
                                    'name': coauthor_name,
                                    'profile_url': coauthor_link
                                })
                        
                        result['coauthors'] = coauthors
                        
                        return result
            
            # Om vi får 429 Too Many Requests, vänta längre tid mellan försöken
            if response.status_code == 429:
                time.sleep(5 * (attempt + 1))
            else:
                time.sleep(1)  # Kort paus mellan försök
        
        # Om vi inte hittar profilen
        return {
            'name': researcher_name,
            'profile_url': "",
            'citations': 0,
            'h_index': 0,
            'i10_index': 0,
            'affiliation': "",
            'interests': [],
            'coauthors': [],
            'search_method': 'no_results'
        }
        
    except Exception as e:
        st.warning(f"Kunde inte söka Google Scholar: {str(e)}")
        return {
            'name': researcher_name,
            'profile_url': "",
            'citations': 0,
            'h_index': 0,
            'i10_index': 0,
            'affiliation': "",
            'interests': [],
            'coauthors': [],
            'search_method': 'error'
        }

def fetch_researcher_by_orcid(orcid):
    """Hämta forskare direkt via ORCID-ID."""
    try:
        st.info(f"Hämtar information för ORCID: {orcid}")
        
        # Använd OrcidClient för att hämta komplett information om forskaren
        researcher = orcid_client.get_researcher_info(orcid, include_details=True)
        
        if not researcher:
            st.warning(f"Kunde inte hitta information för ORCID: {orcid}")
            return None
            
        # Skapa formaterad forskare
        formatted_researcher = {
            'orcid': orcid,
            'namn': researcher.get('given_name', ''),
            'efternamn': researcher.get('family_name', ''),
            'institution': researcher.get('institution', '')
        }
        
        # Kontrollera om vi fick namn
        if not formatted_researcher['namn'] and not formatted_researcher['efternamn']:
            # Försök med fullständigt namn
            full_name = researcher.get('name', '')
            if full_name:
                name_parts = full_name.split(' ', 1)
                if len(name_parts) > 1:
                    formatted_researcher['namn'] = name_parts[0]
                    formatted_researcher['efternamn'] = name_parts[1]
                else:
                    formatted_researcher['namn'] = full_name
        
        return formatted_researcher
    
    except Exception as e:
        st.error(f"Ett fel uppstod vid hämtning via ORCID: {str(e)}")
        import traceback
        st.error(traceback.format_exc())
        return None

def main():
    """Huvudfunktion som kör applikationen."""
    initialize_session_state()
    
    # Initiera current_page om den inte finns
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 'start'
    
    # Navigation till tillbaka/hem-knapp
    if st.session_state.current_page != 'start':
        if st.button("← Hem", use_container_width=False):
            st.session_state.current_page = 'start'
            st.rerun()
    
    # Startsida med två stora knappar
    if st.session_state.current_page == 'start':
        st.title("🔎 Forskardatabas")
        st.markdown("### Välkommen till verktyget för forskardata och publikationer")
        
        # Två stora knappar på startsidan
        col1, col2 = st.columns(2)
        
        with col1:
            forskardatabas_button = st.button("🔍 Forskardatabas", 
                                               use_container_width=True, 
                                               help="Sök och utforska forskare i permanenta databasen")
            if forskardatabas_button:
                st.session_state.current_page = 'forskardatabas'
                # Återställ ytterligare navigationstillstånd
                st.session_state.current_view = "search"
                if 'selected_researcher_id' in st.session_state:
                    del st.session_state['selected_researcher_id']
                st.rerun()
            
            st.markdown("""
            **Forskardatabasen** innehåller alla sparade forskare:
            - Sök bland forskare
            - Se detaljerade profiler
            - Granska publikationer
            - Se forskningsstatistik
            """)
        
        with col2:
            leta_button = st.button("🔎 Leta & Lägg till forskare", 
                                    use_container_width=True,
                                    help="Sök efter nya forskare och hantera arbetsytan")
            if leta_button:
                st.session_state.current_page = 'leta_forskare'
                st.rerun()
            
            st.markdown("""
            **Leta & Lägg till** hjälper dig att:
            - Söka i ORCID och PubMed
            - Hantera arbetsytan
            - Lägga till forskare manuellt
            - Importera från Excel
            """)
        
        # Statistik på startsidan
        st.divider()
        st.subheader("📊 Snabbstatistik")
        
        col1, col2, col3 = st.columns(3)
        
        try:
            with col1:
                # Antal forskare i permanenta databasen
                result = pd.read_sql("SELECT COUNT(*) as antal FROM forskare_permanent", permanent_engine)
                antal = result['antal'].iloc[0] if not result.empty else 0
                st.metric("Forskare i databasen", antal)
                
            with col2:
                # Antal forskare i arbetsytan
                result = pd.read_sql("SELECT COUNT(*) as antal FROM forskare_cleanup", staging_engine)
                antal_arbetsyta = result['antal'].iloc[0] if not result.empty else 0
                st.metric("Forskare i arbetsytan", antal_arbetsyta)
                
            with col3:
                # Senaste uppdateringen
                result = pd.read_sql("SELECT MAX(last_updated) as senast FROM forskare_profiler", permanent_engine)
                senast = result['senast'].iloc[0] if not result.empty else "Aldrig"
                st.metric("Senaste uppdatering", senast)
        except Exception as e:
            st.info("Inga statistikdata tillgängliga ännu")
    
    # Sida för forskardatabasen
    elif st.session_state.current_page == 'forskardatabas':
        # Skapa två vyer: översikt eller detaljvy för forskare
        if st.session_state.get("current_view") == "researcher_detail" and st.session_state.get("selected_researcher_id") is not None:
            # Visa detaljerad forskarprofil
            show_researcher_detail_view()
        else:
            # Visa huvudsökvy
            st.title("Forskardatabas")
            
            # TA BORT PUBMED SÖKNING FRÅN HUVUDNIVÅN
            # Lägg till separat PubMed-sökning här för direktåtkomst
            # if st.expander("🔬 Direktsök i PubMed", expanded=False):
            #    ...
            
            # Skapa flikar för olika sätt att hitta forskare
            search_tabs = st.tabs(["🔍 Sök forskare", "🕒 Senaste sökningar", "➕ Senast tillagda", "📊 Statistik"])
            
            with search_tabs[0]:
                search_col1, search_col2 = st.columns([3, 1])
                
                with search_col1:
                    search_term = st.text_input("Sök på namn, ORCID eller institution", key="search_term_input")
                
                with search_col2:
                    search_button = st.button("Sök", use_container_width=True)
                
                if search_button and search_term:
                    # Spara sökningen i historiken
                    if search_term not in st.session_state['search_history']:
                        st.session_state['search_history'].insert(0, search_term)
                        # Begränsa historiken till de 10 senaste sökningarna
                        st.session_state['search_history'] = st.session_state['search_history'][:10]
                    
                    # Utför sökningen
                    perform_researcher_search(search_term)
                    
                # Om det finns en tidigare sökning och inget nytt har sökts, visa senaste resultaten
                elif 'last_search_results' in st.session_state:
                    st.write("Senaste sökresultat:")
                    display_researcher_list(st.session_state['last_search_results'])
            
            with search_tabs[1]:
                st.subheader("Dina senaste sökningar")
                
                if not st.session_state['search_history']:
                    st.info("Du har inte utfört några sökningar än.")
                else:
                    for idx, search in enumerate(st.session_state['search_history']):
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.write(f"🔍 {search}")
                        with col2:
                            if st.button("Sök igen", key=f"search_again_{idx}"):
                                perform_researcher_search(search)
                        st.divider()
            
            with search_tabs[2]:
                st.subheader("Senast tillagda forskare")
                
                # Hämta de 10 senast tillagda forskarna
                recent_query = """
                SELECT * FROM forskare_permanent 
                ORDER BY created_date DESC 
                LIMIT 10
                """
                try:
                    recent_df = pd.read_sql(recent_query, permanent_engine)
                    display_researcher_list(recent_df)
                except Exception as e:
                    st.error(f"Kunde inte hämta senaste forskare: {str(e)}")
            
            with search_tabs[3]:
                st.subheader("Statistik")
                
                try:
                    # Räkna antal forskare per institution
                    institution_query = """
                    SELECT institution, COUNT(*) as antal
                    FROM forskare_permanent
                    GROUP BY institution
                    ORDER BY antal DESC
                    LIMIT 10
                    """
                    institution_stats = pd.read_sql(institution_query, permanent_engine)
                    
                    if not institution_stats.empty:
                        st.bar_chart(institution_stats.set_index('institution'), use_container_width=True)
                    else:
                        st.info("Ingen statistik tillgänglig ännu.")
                        
                    # Visa statistik om antal med ORCID vs utan
                    orcid_query = """
                    SELECT 
                        CASE 
                            WHEN orcid IS NOT NULL AND orcid != '' THEN 'Har ORCID' 
                            ELSE 'Saknar ORCID' 
                        END as orcid_status,
                        COUNT(*) as antal
                    FROM forskare_permanent
                    GROUP BY orcid_status
                    """
                    orcid_stats = pd.read_sql(orcid_query, permanent_engine)
                    
                    if not orcid_stats.empty:
                        st.subheader("ORCID-statistik")
                        st.bar_chart(orcid_stats.set_index('orcid_status'), use_container_width=True)
                    
                except Exception as e:
                    st.error(f"Kunde inte läsa statistik: {str(e)}")
                    st.info("Försäkra dig om att databasen innehåller data och att tabellerna har rätt struktur.")
    
    # Sida för Leta & Lägg till forskare
    elif st.session_state.current_page == 'leta_forskare':
        st.title("Leta & lägg till forskare")
        
        # Skapa flikar för olika sätt att hitta/lägga till forskare
        leta_tabs = st.tabs(["📋 Arbetsyta", "🔍 Sök ORCID", "🔬 Sök PubMed", "➕ Manuell inmatning", "📤 Importera från Excel"])
        
        # Arbetsyta - Här ser man forskare som har lagts till men inte flyttats till permanenta databasen
        with leta_tabs[0]:
            st.header("Arbetsyta")
            
            st.markdown("""
            Här kan du hantera forskare som du är intresserad av att arbeta med innan de flyttas till den permanenta databasen. 
            Använd flikarna ovan för att lägga till fler forskare.
            """)
            
            # Hämta alla forskare i arbetsytan
            try:
                # Försök först med id-kolumnen
                query = "SELECT id, namn, efternamn, orcid, institution, email, notes FROM forskare_cleanup ORDER BY efternamn, namn"
                df = pd.read_sql(query, staging_engine)
            except Exception as e:
                # Om det misslyckas, använd rowid istället
                query = "SELECT rowid as id, namn, efternamn, orcid, institution, email, notes FROM forskare_cleanup ORDER BY efternamn, namn"
                try:
                    df = pd.read_sql(query, staging_engine)
                except Exception as e:
                    st.error(f"Kunde inte hämta forskare: {str(e)}")
                    # Fallback om något går fel
                    df = pd.DataFrame()
            
            if not df.empty:
                st.write(f"**{len(df)} forskare i arbetsytan**")
                
                # Lägg till knappar för att hantera valda forskare
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    if st.button("Flytta valda till databas", use_container_width=True):
                        # Hämta valda forskare
                        selected_ids = []
                        for key, value in st.session_state.items():
                            if key.startswith("select_") and value == True:
                                try:
                                    researcher_id = int(key.split('_')[1])
                                    selected_ids.append(researcher_id)
                                except (IndexError, ValueError):
                                    pass
                        
                        if selected_ids:
                            success_count = 0
                            failure_messages = []
                            
                            for researcher_id in selected_ids:
                                success, message = move_to_permanent_db(researcher_id, staging_engine)
                                if success:
                                    success_count += 1
                                else:
                                    failure_messages.append(message)
                            
                            if success_count > 0:
                                st.success(f"{success_count} forskare flyttades till databasen")
                            
                            if failure_messages:
                                st.warning("Problem vid flytt av vissa forskare:")
                                for msg in failure_messages:
                                    st.write(f"• {msg}")
                            
                            # Ladda om listan om något lyckades
                            if success_count > 0:
                                st.rerun()
                        else:
                            st.warning("Inga forskare valda")
                
                with col2:
                    if st.button("Ta bort valda", use_container_width=True):
                        # Hämta valda forskare
                        selected_ids = []
                        for key, value in st.session_state.items():
                            if key.startswith("select_") and value == True:
                                try:
                                    researcher_id = int(key.split('_')[1])
                                    selected_ids.append(researcher_id)
                                except (IndexError, ValueError):
                                    pass
                        
                        if selected_ids:
                            # Visa bekräftelsedialog
                            st.warning(f"Vill du verkligen ta bort {len(selected_ids)} forskare från arbetsytan?")
                            
                            confirm_col1, confirm_col2 = st.columns(2)
                            with confirm_col1:
                                if st.button("✓ Ja, ta bort", key="confirm_delete"):
                                    # Genomför borttagning
                                    success_count = 0
                                    error_count = 0
                                    
                                    for researcher_id in selected_ids:
                                        try:
                                            with staging_engine.connect() as conn:
                                                # Använd explicit transaktion
                                                conn.execute(text("BEGIN TRANSACTION"))
                                                # Visa SQL för felsökning
                                                delete_sql = f"DELETE FROM forskare_cleanup WHERE rowid = {researcher_id}"
                                                st.info(f"Kör SQL: {delete_sql}")
                                                # Kör borttagningen
                                                result = conn.execute(text(delete_sql))
                                                # Kontrollera om något togs bort
                                                if result.rowcount > 0:
                                                    success_count += 1
                                                conn.execute(text("COMMIT"))
                                        except Exception as e:
                                            error_count += 1
                                            st.error(f"Fel vid borttagning av forskare {researcher_id}: {str(e)}")
                                            # Försök med alternativ metod
                                            try:
                                                with staging_engine.connect() as conn:
                                                    conn.execute(text(f"DELETE FROM forskare_cleanup WHERE id = {researcher_id}"))
                                                    conn.commit()
                                                    success_count += 1
                                            except Exception as inner_e:
                                                st.error(f"Även alternativ metod misslyckades: {str(inner_e)}")
                                    
                                    if success_count > 0:
                                        st.success(f"Tog bort {success_count} forskare")
                                        # Rensa valda checkboxar
                                        for key in list(st.session_state.keys()):
                                            if key.startswith("select_"):
                                                del st.session_state[key]
                                        time.sleep(1)  # Kort paus så användaren hinner se meddelandet
                                        st.rerun()
                                    else:
                                        st.error(f"Kunde inte ta bort några forskare. Kontakta administratören.")
                                    
                                with confirm_col2:
                                    if st.button("✗ Avbryt", key="cancel_delete"):
                                        st.info("Borttagning avbruten")
                                        st.rerun()
                        else:
                            st.warning("Inga forskare valda")
                
                with col3:
                    if st.button("Redigera vald", use_container_width=True):
                        # Hämta valda forskare
                        selected_ids = []
                        for key, value in st.session_state.items():
                            if key.startswith("select_") and value == True:
                                try:
                                    researcher_id = int(key.split('_')[1])
                                    selected_ids.append(researcher_id)
                                except (IndexError, ValueError):
                                    pass
                        
                        if len(selected_ids) == 1:
                            # Lagra ID för den valda forskaren i session state
                            st.session_state.edit_researcher_id = selected_ids[0]
                            st.session_state.show_edit_form = True
                            st.rerun()
                        elif len(selected_ids) > 1:
                            st.warning("Välj endast en forskare för redigering")
                        else:
                            st.warning("Ingen forskare vald")
                
                # Lista forskare
                st.subheader("Forskare i arbetsytan")
                
                for i, row in df.iterrows():
                    # Använd row['id'] från SQL-frågan
                    row_id = row['id']
                    
                    col1, col2, col3, col4 = st.columns([0.1, 0.3, 0.3, 0.3])
                    
                    with col1:
                        checkbox_key = f"select_{row_id}"
                        # Initiera session state om den inte finns innan vi visar checkboxen
                        if checkbox_key not in st.session_state:
                            st.session_state[checkbox_key] = False
                        selected = st.checkbox("Välj", key=checkbox_key)
                    
                    with col2:
                        # Säkerställ att vi hanterar tomma namn
                        fname = row['namn'] if pd.notna(row['namn']) else ''
                        lname = row['efternamn'] if pd.notna(row['efternamn']) else ''
                        full_name = f"{fname} {lname}".strip()
                        if not full_name:
                            full_name = "Okänt namn"
                        st.write(f"**{full_name}**")
                    
                    with col3:
                        # Visa institution om den finns, annars "Okänd"
                        institution = row['institution'] if pd.notna(row['institution']) else 'Okänd institution'
                        st.write(institution)
                    
                    with col4:
                        orcid = row['orcid'] if pd.notna(row['orcid']) else ''
                        if orcid:
                            st.write(f"[{orcid}](https://orcid.org/{orcid})")
                        else:
                            st.write("Saknas ORCID")
                
                # Visa redigeringsformulär om en forskare är vald för redigering
                if 'show_edit_form' in st.session_state and st.session_state.show_edit_form:
                    st.subheader("Redigera forskare")
                    
                    # Använd rowid för kompatibilitet
                    query = f"SELECT rowid as id, * FROM forskare_cleanup WHERE rowid = {st.session_state.edit_researcher_id}"
                    try:
                        researcher_df = pd.read_sql(query, staging_engine)
                    except Exception as e:
                        st.error(f"Kunde inte hämta forskare: {str(e)}")
                        researcher_df = pd.DataFrame()
                    
                    if not researcher_df.empty:
                        selected_researcher = researcher_df.iloc[0]
                        
                        # Redigera forskarens data
                        new_name = st.text_input("Förnamn", selected_researcher['namn'] if pd.notna(selected_researcher['namn']) else '')
                        new_lastname = st.text_input("Efternamn", selected_researcher['efternamn'] if pd.notna(selected_researcher['efternamn']) else '')
                        new_institution = st.text_input("Institution", selected_researcher['institution'] if pd.notna(selected_researcher['institution']) else '')
                        new_email = st.text_input("Email", selected_researcher['email'] if pd.notna(selected_researcher['email']) else "")
                        new_orcid = st.text_input("ORCID", selected_researcher['orcid'] if pd.notna(selected_researcher['orcid']) else "")
                        new_notes = st.text_area("Noteringar", selected_researcher['notes'] if pd.notna(selected_researcher['notes']) else "")
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("Spara ändringar"):
                                with staging_engine.connect() as conn:
                                    # Använd rowid för uppdatering
                                    conn.execute(text(f"""
                                    UPDATE forskare_cleanup 
                                    SET namn = '{new_name}', 
                                        efternamn = '{new_lastname}', 
                                        institution = '{new_institution}', 
                                        email = '{new_email}', 
                                        orcid = '{new_orcid}',
                                        notes = '{new_notes}' 
                                    WHERE rowid = {st.session_state.edit_researcher_id}
                                    """))
                                    conn.commit()
                                st.success("Forskarens data har uppdaterats")
                                st.session_state.show_edit_form = False
                                st.rerun()
                        
                        with col2:
                            if st.button("Avbryt"):
                                st.session_state.show_edit_form = False
                                st.rerun()
                    else:
                        st.error("Kunde inte hitta forskaren. Försök igen.")
                        st.session_state.show_edit_form = False
            else:
                st.info("Inga forskare i arbetsytan ännu.")
                st.write("Använd flikarna ovan för att lägga till forskare till arbetsytan.")
        
        # ORCID-sökning
        with leta_tabs[1]:
            st.header("Sök och lägg till forskare från ORCID")
            
            col1, col2 = st.columns([3, 1])
            with col1:
                orcid_search = st.text_input("Sök efter forskare (namn, institution, etc.)", key="orcid_search_term")
            
            with col2:
                search_button = st.button("Sök ORCID", use_container_width=True)
            
            if search_button and orcid_search:
                # Sök efter forskare i ORCID
                with st.spinner("Söker efter forskare..."):
                    researchers = search_orcid_researchers(orcid_search, max_results=10)
                    
                    if researchers:
                        st.session_state['orcid_search_results'] = researchers
                        st.success(f"Hittade {len(researchers)} forskare")
                    else:
                        st.warning("Inga forskare hittades")
            
            # Visa resultat om de finns
            if 'orcid_search_results' in st.session_state and st.session_state['orcid_search_results']:
                st.subheader("Sökresultat")
                
                for idx, researcher in enumerate(st.session_state['orcid_search_results']):
                    col1, col2, col3, col4 = st.columns([0.1, 0.3, 0.3, 0.3])
                    
                    # Generera ett unikt nyckelvärde för varje forskare
                    orcid_id = researcher.get('orcid', f"noid_{idx}")
                    checkbox_key = f"orcid_select_{orcid_id}"
                    
                    with col1:
                        # Initiera session state för checkbox om den inte finns
                        if checkbox_key not in st.session_state:
                            st.session_state[checkbox_key] = False
                        
                        selected = st.checkbox("Välj", key=checkbox_key)
                    
                    with col2:
                        full_name = f"{researcher.get('namn', '')} {researcher.get('efternamn', '')}".strip()
                        if not full_name:
                            full_name = "Okänt namn"
                        st.write(f"**{full_name}**")
                    
                    with col3:
                        institution = researcher.get('institution', "Okänd institution")
                        st.write(institution)
                    
                    with col4:
                        orcid = researcher.get('orcid', '')
                        if orcid:
                            st.write(f"[{orcid}](https://orcid.org/{orcid})")
                        else:
                            st.write("Saknas ORCID")
                
                # Knapp för att lägga till valda forskare
                if st.button("Lägg till valda forskare till arbetsytan"):
                    selected_researchers = []
                    for researcher in st.session_state['orcid_search_results']:
                        orcid_id = researcher.get('orcid', f"noid_{st.session_state['orcid_search_results'].index(researcher)}")
                        checkbox_key = f"orcid_select_{orcid_id}"
                        if checkbox_key in st.session_state and st.session_state[checkbox_key]:
                            selected_researchers.append(researcher)
                    
                    if selected_researchers:
                        # Spara forskarna i den temporära databasen
                        success = save_to_database(selected_researchers, engine=staging_engine)
                        if success:
                            st.success(f"{len(selected_researchers)} forskare har lagts till i arbetsytan")
                            # Rensa valda checkboxar
                            for researcher in st.session_state['orcid_search_results']:
                                orcid_id = researcher.get('orcid', f"noid_{st.session_state['orcid_search_results'].index(researcher)}")
                                checkbox_key = f"orcid_select_{orcid_id}"
                                if checkbox_key in st.session_state:
                                    st.session_state[checkbox_key] = False
                    else:
                        st.warning("Inga forskare valda")
        
        # PubMed-sökning
        with leta_tabs[2]:
            st.header("Sök i PubMed")
            
            # Sökfält och knapp
            col1, col2 = st.columns([3, 1])
            with col1:
                pubmed_query = st.text_input("Sök efter publikationer", 
                                        key="pubmed_search_term",
                                        help="Du kan använda avancerade söktermer som author:namn, title:ord, etc.")
            
            with col2:
                pubmed_button = st.button("Sök i PubMed", key="pubmed_search_button", use_container_width=True)
            
            # Utför sökning om knappen klickas
            if pubmed_button and pubmed_query:
                with st.spinner("Söker i PubMed..."):
                    articles = search_pubmed(pubmed_query, 20)
                    
                    if articles:
                        st.session_state['pubmed_results'] = articles
                        st.success(f"Hittade {len(articles)} publikationer")
                    else:
                        st.warning("Inga publikationer hittades")
            
            # Visa resultat om de finns
            if 'pubmed_results' in st.session_state and st.session_state['pubmed_results']:
                st.subheader("Sökresultat")
                
                # Skapa DataFrame med resultaten för bättre visning
                articles_df = pd.DataFrame(st.session_state['pubmed_results'])
                
                if 'title' in articles_df.columns:
                    # Visa snyggare tabell med viktiga kolumner
                    formatted_df = articles_df[['title', 'authors', 'journal', 'publication_date', 'pmid']]
                    formatted_df.columns = ['Titel', 'Författare', 'Journal', 'Publiceringsdatum', 'PMID']
                    st.dataframe(formatted_df, use_container_width=True)
                    
                    # Visa detaljvy för en vald publikation
                    selected_article = st.selectbox("Välj en publikation för att se detaljer:", 
                                                   [article['title'] for article in st.session_state['pubmed_results']])
                    
                    if selected_article:
                        article = next(a for a in st.session_state['pubmed_results'] if a['title'] == selected_article)
                        
                        st.markdown(f"### {article['title']}")
                        st.markdown(f"**Författare:** {article['authors']}")
                        st.markdown(f"**Journal:** {article['journal']}")
                        st.markdown(f"**Publiceringsdatum:** {article['publication_date']}")
                        st.markdown(f"**PMID:** [{article['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/)")
                        
                        if 'abstract' in article and article['abstract']:
                            st.markdown("#### Abstract")
                            st.markdown(article['abstract'])
                        
                        # Lägg till knapp för att hitta författare
                        if st.button("Sök efter författare i ORCID"):
                            # Extrahera författarnamn
                            authors = article['authors'].split(", ")
                            if authors and len(authors) > 0:
                                # Ta första författaren
                                first_author = authors[0]
                                # Sätt sökterm och ändra till ORCID-sökning
                                st.session_state['orcid_search_term'] = first_author
                                st.experimental_set_query_params(tab='orcid')
                                # Ändra till ORCID-fliken genom att sätta index
                                st.session_state['leta_tab'] = 1  # Andra fliken är ORCID-sökning
                                st.rerun()
                else:
                    # Fallback om strukturen inte matchar förväntningarna
                    st.dataframe(articles_df, use_container_width=True)
        
        # Manuell inmatning av forskare
        with leta_tabs[3]:
            st.header("Manuell inmatning av forskare")
            
            with st.form("add_researcher_form"):
                name = st.text_input("Förnamn")
                lastname = st.text_input("Efternamn")
                orcid = st.text_input("ORCID")
                institution = st.text_input("Institution")
                email = st.text_input("Email")
                notes = st.text_area("Noteringar")
                
                submit_button = st.form_submit_button("Lägg till forskare")
                
                if submit_button:
                    # Validera ORCID-format om angivet
                    if orcid and not validate_orcid(orcid):
                        st.error("Ogiltigt ORCID-format. ORCID ska ha formatet 0000-0000-0000-0000 eller 0000000000000000")
                    else:
                        # Skapa forskardatan och spara
                        researcher_data = {
                            'namn': name,
                            'efternamn': lastname,
                            'orcid': orcid,
                            'institution': institution,
                            'email': email,
                            'notes': notes
                        }
                        
                        success = save_to_database([researcher_data], engine=staging_engine)
                        if success:
                            st.success("Forskare har lagts till i arbetsytan")
        
        # Importera från Excel
        with leta_tabs[4]:
            st.header("Importera forskare från Excel")
            
            uploaded_file = st.file_uploader("Välj Excel-fil", type=["xlsx", "xls"])
            if uploaded_file:
                st.info("""
                Excel-filen bör ha kolumner med namn: 
                'Förnamn'/'namn', 'Efternamn'/'lastname', 'ORCID'/'orcid', 'Institution'/'institution', 'Email'/'email'
                """)
                
                if st.button("Processa fil"):
                    with st.spinner("Bearbetar Excel-fil..."):
                        success, message, researchers = process_excel_file(uploaded_file)
                        if success:
                            st.success(f"{message} ({len(researchers)} forskare)")
                            
                            # Visa en förhandsgranskning av data
                            if researchers:
                                st.subheader("Förhandsgranskning")
                                preview_df = pd.DataFrame(researchers)
                                st.dataframe(preview_df)
                                
                                if st.button("Bekräfta import"):
                                    db_success = save_to_database(researchers, engine=staging_engine)
                                    if db_success:
                                        st.success(f"{len(researchers)} forskare har importerats till arbetsytan")
                        else:
                            st.error(message)

# Initiera session state variabler
def initialize_session_state():
    """Initiera session state variabler för applikationen."""
    if 'search_history' not in st.session_state:
        st.session_state['search_history'] = []
    
    if 'current_view' not in st.session_state:
        st.session_state['current_view'] = "search"

if __name__ == "__main__":
    # Konfigurera ORCID-klienten för att tillåta live-anrop
    orcid_client.debug_mode = False
    
    # Huvudprogrammet startas
    try:
        main()
    except Exception as e:
        st.error(f"Ett oväntat fel uppstod: {str(e)}")
        import traceback
        st.error(traceback.format_exc())
