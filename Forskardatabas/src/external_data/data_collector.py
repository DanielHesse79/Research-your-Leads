import requests
import pandas as pd
import time
import logging
import json
import os
import re
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from bs4 import BeautifulSoup
import functools

# Konfigurera loggning
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Egen implementation av retry-decorator istället för extern dependency
def retry(tries=3, delay=1, backoff=2):
    """
    Retry-decorator som försöker köra funktionen flera gånger vid fel.
    
    Args:
        tries: Antal försök innan den ger upp
        delay: Initial fördröjning mellan försök (sekunder)
        backoff: Multiplikator för fördröjning mellan försök
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            mtries, mdelay = tries, delay
            while mtries > 0:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"Retry: {func.__name__} failed with {str(e)}. Retrying in {mdelay}s...")
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff
                    if mtries == 0:
                        logger.error(f"Failed after {tries} attempts: {func.__name__}")
                        raise
            return func(*args, **kwargs)
        return wrapper
    return decorator

class APIRateLimiter:
    """Hjälpklass för att hantera API-förfrågningsbegränsningar."""
    
    def __init__(self, calls_per_second: float = 1.0):
        """Initiera ratebegränsare med antal förfrågningar per sekund."""
        self.period = 1.0 / calls_per_second
        self.last_call_time = 0
    
    def wait(self):
        """Vänta om nödvändigt för att respektera begränsningar."""
        current_time = time.time()
        time_since_last_call = current_time - self.last_call_time
        
        if time_since_last_call < self.period:
            time_to_wait = self.period - time_since_last_call
            time.sleep(time_to_wait)
        
        self.last_call_time = time.time()


class PubMedCollector:
    """Klass för att samla data från PubMed API."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initiera PubMed-konnektorn med API-nyckel om tillgänglig."""
        self.api_key = api_key
        self.base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        self.rate_limiter = APIRateLimiter(calls_per_second=3)  # PubMed har vanligtvis en begränsning på 3 förfrågningar/sekund
        logger.info("PubMed-konnektorn initierad")
    
    @retry()
    def search_articles(self, query: str, max_results: int = 100) -> List[Dict]:
        """Sök efter artiklar i PubMed baserat på sökfråga."""
        try:
            # Steg 1: Använd esearch för att få artikel-ID:n
            self.rate_limiter.wait()
            search_url = f"{self.base_url}/esearch.fcgi"
            params = {
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retmode": "json"
            }
            
            if self.api_key:
                params["api_key"] = self.api_key
                
            response = requests.get(search_url, params=params)
            response.raise_for_status()
            search_results = response.json()
            
            # Extrahera ID:n
            id_list = search_results.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                logger.warning(f"Inga resultat hittades för sökningen: {query}")
                return []
                
            logger.info(f"Hittade {len(id_list)} artiklar för sökningen: {query}")
            
            # Steg 2: Använd efetch för att hämta detaljerad information
            self.rate_limiter.wait()
            fetch_url = f"{self.base_url}/efetch.fcgi"
            params = {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "xml"
            }
            
            if self.api_key:
                params["api_key"] = self.api_key
                
            response = requests.get(fetch_url, params=params)
            response.raise_for_status()
            
            # Bearbeta XML-respons
            soup = BeautifulSoup(response.text, 'xml')
            articles = []
            
            for article in soup.find_all('PubmedArticle'):
                article_data = {}
                
                # PMID (PubMed ID)
                pmid = article.find('PMID')
                if pmid:
                    article_data['pmid'] = pmid.text
                
                # Titel
                title = article.find('ArticleTitle')
                if title:
                    article_data['title'] = title.text
                
                # Abstract
                abstract_text = article.find('AbstractText')
                if abstract_text:
                    article_data['abstract'] = abstract_text.text
                
                # Författare
                authors = []
                author_list = article.find('AuthorList')
                if author_list:
                    for author in author_list.find_all('Author'):
                        author_name = []
                        last_name = author.find('LastName')
                        if last_name:
                            author_name.append(last_name.text)
                        
                        fore_name = author.find('ForeName')
                        if fore_name:
                            author_name.append(fore_name.text)
                        
                        if author_name:
                            authors.append(" ".join(author_name))
                
                article_data['authors'] = authors
                
                # Publikationsdatum
                pub_date = article.find('PubDate')
                if pub_date:
                    year = pub_date.find('Year')
                    month = pub_date.find('Month')
                    day = pub_date.find('Day')
                    
                    date_parts = []
                    if year:
                        date_parts.append(year.text)
                    if month:
                        date_parts.append(month.text)
                    if day:
                        date_parts.append(day.text)
                    
                    article_data['publication_date'] = "-".join(date_parts)
                
                # Tidskrift
                journal = article.find('Journal')
                if journal:
                    journal_title = journal.find('Title')
                    if journal_title:
                        article_data['journal'] = journal_title.text
                
                # DOI
                article_id_list = article.find('ArticleIdList')
                if article_id_list:
                    for article_id in article_id_list.find_all('ArticleId'):
                        if article_id.get('IdType') == 'doi':
                            article_data['doi'] = article_id.text
                
                articles.append(article_data)
            
            logger.info(f"Hämtade detaljer för {len(articles)} artiklar")
            return articles
            
        except Exception as e:
            logger.error(f"Fel vid sökning i PubMed: {str(e)}")
            raise
    
    def search_by_orcid(self, orcid: str, max_results: int = 100) -> List[Dict]:
        """Sök efter artiklar i PubMed kopplade till ett specifikt ORCID-ID."""
        query = f"{orcid}[auid]"  # auid = Author Identifier
        return self.search_articles(query, max_results)
    
    def to_dataframe(self, articles: List[Dict]) -> pd.DataFrame:
        """Konvertera artikeldata till en Pandas DataFrame."""
        # Expandera författarlistan till en sträng för enklare hantering i DataFrame
        for article in articles:
            if 'authors' in article:
                article['authors_str'] = ", ".join(article['authors'])
        
        return pd.DataFrame(articles)


class GoogleScholarCollector:
    """Klass för att samla data från Google Scholar via scraping (notera: kan bryta mot användarvillkor)."""
    
    def __init__(self):
        """Initiera Google Scholar-konnektorn."""
        self.base_url = "https://scholar.google.com/scholar"
        self.rate_limiter = APIRateLimiter(calls_per_second=0.2)  # Försiktig för att undvika att bli blockerad
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        logger.info("Google Scholar-konnektorn initierad (använd med försiktighet)")
    
    @retry()
    def search_articles(self, query: str, max_results: int = 10) -> List[Dict]:
        """Sök efter artiklar i Google Scholar baserat på sökfråga."""
        try:
            articles = []
            
            # Beräkna antal sidor baserat på max_results (10 resultat per sida)
            pages = (max_results + 9) // 10  # Avrunda uppåt
            
            for page in range(pages):
                self.rate_limiter.wait()
                
                params = {
                    "q": query,
                    "start": page * 10
                }
                
                response = requests.get(self.base_url, params=params, headers=self.headers)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extrahera artiklar från aktuell sida
                for result in soup.select('.gs_r.gs_or.gs_scl'):
                    article_data = {}
                    
                    # Titel och länk
                    title_elem = result.select_one('.gs_rt a')
                    if title_elem:
                        article_data['title'] = title_elem.text
                        article_data['url'] = title_elem.get('href')
                    
                    # Författare, tidskrift, år
                    subtitle = result.select_one('.gs_a')
                    if subtitle:
                        subtitle_text = subtitle.text
                        article_data['meta_info'] = subtitle_text
                        
                        # Försök extrahera författare
                        author_match = re.match(r'^(.+?) - ', subtitle_text)
                        if author_match:
                            article_data['authors_str'] = author_match.group(1)
                        
                        # Försök extrahera år
                        year_match = re.search(r'\b(19|20)\d{2}\b', subtitle_text)
                        if year_match:
                            article_data['year'] = year_match.group(0)
                    
                    # Utdrag/sammanfattning
                    snippet = result.select_one('.gs_rs')
                    if snippet:
                        article_data['snippet'] = snippet.text
                    
                    # Citerad av
                    cited_by = result.select_one('a:contains("Cited by")')
                    if cited_by:
                        cited_by_text = cited_by.text
                        citations_match = re.search(r'\d+', cited_by_text)
                        if citations_match:
                            article_data['citations'] = int(citations_match.group(0))
                    
                    articles.append(article_data)
                    
                    # Avbryt om vi nått max_results
                    if len(articles) >= max_results:
                        break
                
                # Avbryt om vi nått max_results
                if len(articles) >= max_results:
                    break
            
            logger.info(f"Hämtade {len(articles)} artiklar från Google Scholar för sökningen: {query}")
            return articles[:max_results]  # Begränsa till önskat antal
            
        except Exception as e:
            logger.error(f"Fel vid sökning i Google Scholar: {str(e)}")
            raise
    
    def search_by_author(self, author: str, max_results: int = 10) -> List[Dict]:
        """Sök efter artiklar i Google Scholar skrivna av en specifik författare."""
        query = f"author:\"{author}\""
        return self.search_articles(query, max_results)
    
    def to_dataframe(self, articles: List[Dict]) -> pd.DataFrame:
        """Konvertera artikeldata till en Pandas DataFrame."""
        return pd.DataFrame(articles)


class OrcidClient:
    """Klass för att interagera med ORCID API och matcha forskare."""
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """Initiera ORCID-klienten med klientuppgifter om tillgängliga."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://pub.orcid.org/v3.0"
        # Minska anropsfrekvensen avsevärt för att förhindra överbelastning
        self.rate_limiter = APIRateLimiter(calls_per_second=0.1)  # Max 1 anrop var 10:e sekund
        self.token = None
        # Lägger till debug-flagga för att undvika kontinuerliga anrop
        self.debug_mode = False
        
        self.headers = {
            "Accept": "application/json"
        }
        
        if client_id and client_secret:
            self._get_token()
        
        logger.info("ORCID-klienten initierad")
    
    def _get_token(self):
        """Hämta åtkomsttoken om klientuppgifter är tillgängliga."""
        try:
            token_url = "https://orcid.org/oauth/token"
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                "scope": "/read-public"
            }
            
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.token = token_data.get("access_token")
            
            if self.token:
                self.headers["Authorization"] = f"Bearer {self.token}"
                logger.info("ORCID-token erhållen")
            else:
                logger.warning("Kunde inte hämta ORCID-token")
                
        except Exception as e:
            logger.error(f"Fel vid hämtning av ORCID-token: {str(e)}")
    
    @retry()
    def get_researcher_info(self, orcid: str, include_details: bool = False) -> Optional[Dict]:
        """
        Hämta information om en forskare baserat på ORCID-ID.
        
        Args:
            orcid: ORCID-identifierare
            include_details: Om True, hämta detaljerad information inklusive
                             alla verk, anställningar, utbildning, finansiering, etc.
        """
        try:
            # Om vi är i debug-läge, returnera en enkel forskarprofil för att undvika onödiga anrop
            if self.debug_mode:
                logger.warning(f"ORCID-profil-hämtning ignorerad i debug-läge för {orcid}")
                return {
                    'orcid_id': orcid,
                    'name': 'Debug Mode',
                    'given_name': 'Debug',
                    'family_name': 'Mode',
                    'institution': 'Debug Institution'
                }
                
            self.rate_limiter.wait()
            
            url = f"{self.base_url}/{orcid}"
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            if not data:
                logger.warning(f"Tomt svar från ORCID API för {orcid}")
                return None
            
            # Extrahera relevant information
            researcher = {
                'orcid_id': orcid  # Alltid inkludera ORCID-ID i resultat
            }
            
            # Grundläggande information
            person = data.get("person", {}) or {}
            
            # Namn
            name = person.get("name", {}) or {}
            if name:
                given_names_obj = name.get("given-names", {}) or {}
                family_name_obj = name.get("family-name", {}) or {}
                
                given_names = given_names_obj.get("value") if given_names_obj else ""
                family_name = family_name_obj.get("value") if family_name_obj else ""
                
                if given_names and family_name:
                    researcher["name"] = f"{given_names} {family_name}"
                elif given_names:
                    researcher["name"] = given_names
                elif family_name:
                    researcher["name"] = family_name
                
                researcher["given_name"] = given_names
                researcher["family_name"] = family_name
                
                # Krediteringsnamn (användarnamn i ORCID)
                credit_name_obj = name.get("credit-name", {}) or {}
                credit_name = credit_name_obj.get("value") if credit_name_obj else ""
                if credit_name:
                    researcher["credit_name"] = credit_name
            
            # Biografi
            biography_obj = person.get("biography", {}) or {}
            biography = biography_obj.get("content") if biography_obj else ""
            if biography:
                researcher["biography"] = biography
            
            # Keywords/Forskningsområden
            keywords_obj = person.get("keywords", {}) or {}
            keywords = keywords_obj.get("keyword", []) or []
            if keywords:
                researcher["keywords"] = []
                for k in keywords:
                    if k and isinstance(k, dict) and "content" in k:
                        researcher["keywords"].append(k.get("content"))
            
            # Andra namn
            other_names_container = person.get("other-names", {}) or {}
            other_name_list = other_names_container.get("other-name", []) or []
            if other_name_list:
                researcher["other_names"] = []
                for name_item in other_name_list:
                    if name_item and isinstance(name_item, dict) and "content" in name_item:
                        researcher["other_names"].append(name_item["content"])
            
            # Kontaktinformation
            contact_info = {}
            
            # E-postadresser
            emails_obj = person.get("emails", {}) or {}
            emails = emails_obj.get("email", []) or []
            if emails:
                contact_info["emails"] = []
                for email in emails:
                    if email and isinstance(email, dict):
                        contact_info["emails"].append({
                            "email": email.get("email", ""),
                            "visibility": email.get("visibility", ""),
                            "verified": email.get("verified", False),
                            "primary": email.get("primary", False)
                        })
            
            # Adresser
            addresses_container = person.get("addresses", {}) or {}
            addresses = addresses_container.get("address", []) or []
            if addresses:
                contact_info["addresses"] = []
                for address in addresses:
                    if address and isinstance(address, dict):
                        country_obj = address.get("country", {}) or {}
                        contact_info["addresses"].append({
                            "country": country_obj.get("value", ""),
                            "visibility": address.get("visibility", "")
                        })
            
            if contact_info:
                researcher["contact"] = contact_info
            
            # AKTIVITETER
            activities = data.get("activities-summary", {}) or {}
            
            # Institutioner/Organisationer (Anställningar)
            employments = []
            employments_container = activities.get("employments", {}) or {}
            employment_list = employments_container.get("employment-summary", []) or []
            
            if employment_list:
                for emp in employment_list:
                    if not emp or not isinstance(emp, dict):
                        continue
                        
                    org = emp.get("organization", {}) or {}
                    org_address = org.get("address", {}) or {}
                    disambiguated_org = org.get("disambiguated-organization", {}) or {}
                    
                    employment = {
                        "organization": org.get("name", ""),
                        "department": emp.get("department-name", ""),
                        "role": emp.get("role-title", ""),
                        "location": {
                            "city": org_address.get("city", ""),
                            "region": org_address.get("region", ""),
                            "country": org_address.get("country", "")
                        }
                    }
                    
                    # Bara inkludera start/slutdatum om include_details är True
                    if include_details:
                        start_date = self._format_date(emp.get("start-date"))
                        end_date = self._format_date(emp.get("end-date"))
                        
                        if start_date:
                            employment["start_date"] = start_date
                        if end_date:
                            employment["end_date"] = end_date
                        
                    employments.append(employment)
                
                researcher["employments"] = employments
                
                # För bakåtkompatibilitet, använd första organisationen som institution
                if employments and len(employments) > 0:
                    researcher["institution"] = employments[0].get("organization", "")
            
            # Om vi vill ha detaljerad information
            if include_details:
                # Utbildningshistorik
                educations = []
                educations_container = activities.get("educations", {}) or {}
                education_list = educations_container.get("education-summary", []) or []
                
                if education_list:
                    for edu in education_list:
                        if not edu or not isinstance(edu, dict):
                            continue
                            
                        org = edu.get("organization", {}) or {}
                        org_address = org.get("address", {}) or {}
                        
                        education = {
                            "organization": org.get("name", ""),
                            "department": edu.get("department-name", ""),
                            "degree": edu.get("role-title", ""),
                            "location": {
                                "city": org_address.get("city", ""),
                                "region": org_address.get("region", ""),
                                "country": org_address.get("country", "")
                            },
                            "start_date": self._format_date(edu.get("start-date")),
                            "end_date": self._format_date(edu.get("end-date"))
                        }
                        educations.append(education)
                
                    researcher["educations"] = educations
                
                # Publikationer - alla detaljer istället för bara sammanfattning
                works = []
                works_container = activities.get("works", {}) or {}
                work_groups = works_container.get("group", []) or []
                
                if work_groups:
                    for work_group in work_groups:
                        if not work_group or not isinstance(work_group, dict):
                            continue
                            
                        work_summaries = work_group.get("work-summary", []) or []
                        for work in work_summaries:
                            if not work or not isinstance(work, dict):
                                continue
                                
                            # Basdata
                            title_container = work.get("title", {}) or {}
                            title_value_container = title_container.get("title", {}) or {}
                            title = title_value_container.get("value", "")
                            
                            journal_container = work.get("journal-title", {}) or {}
                            journal = journal_container.get("value", "")
                            
                            url_container = work.get("url", {}) or {}
                            url = url_container.get("value", "")
                            
                            # Säker datumanvändning
                            pub_date = work.get("publication-date")
                            
                            publication = {
                                "title": title,
                                "type": work.get("type", ""),
                                "publication_date": self._format_date(pub_date),
                                "url": url,
                                "journal": journal,
                                "identifiers": {}
                            }
                            
                            # Externa identifierare (DOI, etc.)
                            external_ids_container = work.get("external-ids", {}) or {}
                            external_ids = external_ids_container.get("external-id", []) or []
                            for ext_id in external_ids:
                                if not ext_id or not isinstance(ext_id, dict):
                                    continue
                                    
                                id_type = ext_id.get("external-id-type", "")
                                id_value = ext_id.get("external-id-value", "")
                                if id_type and id_value:
                                    publication["identifiers"][id_type] = id_value
                            
                            works.append(publication)
                    
                    researcher["works"] = works
                    researcher["publications_count"] = len(works)
                
                # Finansiering och bidrag
                fundings = []
                fundings_container = activities.get("fundings", {}) or {}
                funding_groups = fundings_container.get("group", []) or []
                
                if funding_groups:
                    for funding_group in funding_groups:
                        if not funding_group or not isinstance(funding_group, dict):
                            continue
                            
                        funding_summaries = funding_group.get("funding-summary", []) or []
                        for funding in funding_summaries:
                            if not funding or not isinstance(funding, dict):
                                continue
                                
                            org = funding.get("organization", {}) or {}
                            title_container = funding.get("title", {}) or {}
                            title_value_container = title_container.get("title", {}) or {}
                            title = title_value_container.get("value", "")
                            
                            amount_container = funding.get("amount", {}) or {}
                            
                            funding_info = {
                                "title": title,
                                "type": funding.get("type", ""),
                                "organization": org.get("name", ""),
                                "amount": {
                                    "value": amount_container.get("value", ""),
                                    "currency": amount_container.get("currency-code", "")
                                },
                                "start_date": self._format_date(funding.get("start-date")),
                                "end_date": self._format_date(funding.get("end-date"))
                            }
                            
                            # Externa identifierare
                            external_ids_container = funding.get("external-ids", {}) or {}
                            external_ids = external_ids_container.get("external-id", []) or []
                            funding_info["identifiers"] = {}
                            
                            for ext_id in external_ids:
                                if not ext_id or not isinstance(ext_id, dict):
                                    continue
                                    
                                id_type = ext_id.get("external-id-type", "")
                                id_value = ext_id.get("external-id-value", "")
                                if id_type and id_value:
                                    funding_info["identifiers"][id_type] = id_value
                            
                            fundings.append(funding_info)
                    
                    researcher["fundings"] = fundings
                
                # Medlemskap och tjänster (services)
                services = []
                services_container = activities.get("services", {}) if "services" in activities else {}
                service_summaries = services_container.get("service-summary", []) or []
                
                if service_summaries:
                    for service_group in service_summaries:
                        if not service_group or not isinstance(service_group, dict):
                            continue
                            
                        org = service_group.get("organization", {}) or {}
                        service = {
                            "organization": org.get("name", ""),
                            "role": service_group.get("role-title", ""),
                            "start_date": self._format_date(service_group.get("start-date")),
                            "end_date": self._format_date(service_group.get("end-date"))
                        }
                        services.append(service)
                    
                    researcher["services"] = services
                
                # Externa identifierare
                external_identifiers = []
                external_identifiers_container = person.get("external-identifiers", {}) or {}
                ext_identifiers = external_identifiers_container.get("external-identifier", []) or []
                
                if ext_identifiers:
                    for ext_id in ext_identifiers:
                        if not ext_id or not isinstance(ext_id, dict):
                            continue
                            
                        ext_id_url = ext_id.get("external-id-url", {}) or {}
                        
                        external_identifiers.append({
                            "type": ext_id.get("external-id-type", ""),
                            "value": ext_id.get("external-id-value", ""),
                            "url": ext_id_url.get("value", "")
                        })
                    
                    researcher["external_identifiers"] = external_identifiers
                
            else:
                # Om vi inte vill ha detaljerad information, hämta bara sammanfattningar
                works_container = activities.get("works", {}) or {}
                works = works_container.get("group", []) or []
                
                if works:
                    researcher["publications_count"] = len(works)
                    
                    # Hämta lite exempel på publikationer
                    researcher["publications_examples"] = []
                    for i, work_group in enumerate(works[:5]):  # Begränsa till 5 exempel
                        if not work_group or not isinstance(work_group, dict):
                            continue
                            
                        work_summaries = work_group.get("work-summary", []) or []
                        if not work_summaries:
                            continue
                            
                        work_summary = work_summaries[0] if len(work_summaries) > 0 else None
                        if not work_summary or not isinstance(work_summary, dict):
                            continue
                            
                        title_container = work_summary.get("title", {}) or {}
                        title_obj = title_container.get("title", {}) or {}
                        title = title_obj.get("value", "")
                        
                        if title:
                            pub_info = {"title": title}
                            
                            # Publikationstyp
                            pub_type = work_summary.get("type")
                            if pub_type:
                                pub_info["type"] = pub_type
                            
                            # Publikationsår
                            pub_date = work_summary.get("publication-date", {}) or {}
                            year_obj = pub_date.get("year", {}) or {}
                            year = year_obj.get("value")
                            if year:
                                pub_info["year"] = year
                            
                            # DOI om tillgängligt
                            external_ids_container = work_summary.get("external-ids", {}) or {}
                            external_ids = external_ids_container.get("external-id", []) or []
                            for ext_id in external_ids:
                                if ext_id and isinstance(ext_id, dict) and ext_id.get("external-id-type") == "doi":
                                    doi = ext_id.get("external-id-value")
                                    if doi:
                                        pub_info["doi"] = doi
                            
                            researcher["publications_examples"].append(pub_info)
            
            logger.info(f"Hämtade {'detaljerad ' if include_details else ''}information om forskare med ORCID {orcid}")
            return researcher
            
        except Exception as e:
            logger.error(f"Fel vid hämtning av information från ORCID API för {orcid}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _format_date(self, date_obj):
        """Formaterar datum från ORCID API till läsbar sträng."""
        if not date_obj:
            return None
        
        # Säkerställ att year, month och day-objekten inte är None
        year_obj = date_obj.get("year", {}) or {}
        month_obj = date_obj.get("month", {}) or {}
        day_obj = date_obj.get("day", {}) or {}
        
        # Extrahera värdena säkert
        year = year_obj.get("value") if isinstance(year_obj, dict) else None
        month = month_obj.get("value") if isinstance(month_obj, dict) else None
        day = day_obj.get("value") if isinstance(day_obj, dict) else None
        
        if year:
            if month and day:
                return f"{year}-{month}-{day}"
            elif month:
                return f"{year}-{month}"
            else:
                return f"{year}"
        
        return None
    
    @retry()
    def search_researchers(self, query: str, max_results: int = 10) -> List[Dict]:
        """Sök efter forskare baserat på namn eller andra kriterier."""
        try:
            # Om vi är i debug-läge, returnera en tom lista för att undvika onödiga anrop
            if self.debug_mode:
                logger.warning("ORCID-sökning ignorerad i debug-läge")
                return []
                
            self.rate_limiter.wait()
            
            url = f"{self.base_url}/search"
            params = {
                "q": query,
                "rows": max_results
            }
            
            response = requests.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            
            results = []
            for result in data.get("result", []):
                # Extrahera ORCID ID först
                orcid_id = result.get("orcid-identifier", {}).get("path")
                if not orcid_id:
                    continue
                    
                # För stora sökningar, skapa en enkel profil utan att göra ett ytterligare API-anrop
                if max_results > 5:
                    # Extrahera organisation om tillgänglig i sökresultatet
                    institution = ""
                    affiliation_container = result.get("affiliation-path", {})
                    if isinstance(affiliation_container, dict):
                        affiliation_name = affiliation_container.get("affiliation-name", "")
                        if affiliation_name:
                            institution = affiliation_name
                    
                    # Skapa en minimal forskarprofil
                    researcher_info = {
                        "orcid_id": orcid_id,
                        "orcid": orcid_id,  # Dubblera för bakåtkompatibilitet
                        "name": result.get("display-name", ""),
                        "display-name": result.get("display-name", ""),  # Förbättra tillgång till displaynamn
                        "institution": institution
                    }
                    
                    # Försök extrahera förnamn och efternamn från display-name
                    display_name = result.get("display-name", "")
                    if display_name:
                        parts = display_name.split()
                        if len(parts) > 1:
                            researcher_info["given_name"] = parts[0]
                            researcher_info["family_name"] = " ".join(parts[1:])
                        else:
                            researcher_info["given_name"] = display_name
                            researcher_info["family_name"] = ""
                    
                    results.append(researcher_info)
                else:
                    # För mindre sökningar, hämta fullständig information
                    researcher_info = self.get_researcher_info(orcid_id)
                    if researcher_info:
                        # Lägg till både orcid_id och orcid för bakåtkompatibilitet
                        researcher_info["orcid_id"] = orcid_id
                        researcher_info["orcid"] = orcid_id
                        results.append(researcher_info)
            
            logger.info(f"Hittade {len(results)} forskare för sökningen: {query}")
            return results
            
        except Exception as e:
            logger.error(f"Fel vid sökning av forskare via ORCID API: {str(e)}")
            return []
    
    def match_researcher(self, name: str, keywords: Optional[List[str]] = None, 
                         institution: Optional[str] = None) -> Optional[Dict]:
        """Försök matcha en forskare baserat på namn och andra attribut."""
        try:
            # Bygg sökfrågan
            query_parts = [f"\"{name}\""]
            
            if keywords:
                for keyword in keywords[:3]:  # Begränsa till 3 nyckelord för att undvika för specifika sökningar
                    query_parts.append(f"\"{keyword}\"")
            
            if institution:
                query_parts.append(f"\"{institution}\"")
            
            query = " AND ".join(query_parts)
            
            # Sök efter forskare
            researchers = self.search_researchers(query, max_results=5)
            
            if not researchers:
                logger.warning(f"Ingen matchning hittades för: {name}")
                return None
            
            # Om vi har flera matchningar, beräkna matchningskonfidensen och välj den bästa
            if len(researchers) > 1:
                for researcher in researchers:
                    confidence = 0.0
                    
                    # Namn-matchning (enkel jämförelse, kan förbättras)
                    full_name = researcher.get("name", "").lower()
                    if full_name and name.lower() in full_name:
                        confidence += 0.5
                        if name.lower() == full_name:
                            confidence += 0.3
                    
                    # Nyckelords-matchning
                    if keywords and "keywords" in researcher:
                        researcher_keywords = [k.lower() for k in researcher.get("keywords", [])]
                        for keyword in keywords:
                            if keyword.lower() in researcher_keywords:
                                confidence += 0.1
                    
                    # Institutions-matchning
                    if institution and "institution" in researcher:
                        affiliations = [a.lower() for a in researcher.get("institution", "").split()]
                        for affiliation in affiliations:
                            if institution.lower() in affiliation:
                                confidence += 0.2
                    
                    researcher["match_confidence"] = round(min(confidence, 1.0), 2)
                
                # Sortera efter matchningskonfidens
                researchers.sort(key=lambda x: x.get("match_confidence", 0), reverse=True)
            
            # Lägg till matchningsinformation för det första resultatet
            if "match_confidence" not in researchers[0]:
                researchers[0]["match_confidence"] = 0.7  # Standardvärde om bara en matchning hittades
            
            best_match = researchers[0]
            logger.info(f"Matchade {name} till ORCID {best_match.get('orcid')} med konfidens {best_match.get('match_confidence')}")
            
            return best_match
            
        except Exception as e:
            logger.error(f"Fel vid matchning av forskare: {str(e)}")
            return None
    
    def to_dataframe(self, researchers: List[Dict]) -> pd.DataFrame:
        """Konvertera forskarprofiler till en Pandas DataFrame."""
        return pd.DataFrame(researchers)


# Exempel på användning
if __name__ == "__main__":
    # Exempel på PubMed-sökning
    pubmed = PubMedCollector()
    articles = pubmed.search_articles("genomics AND cancer", max_results=5)
    df_pubmed = pubmed.to_dataframe(articles)
    print(f"PubMed-sökning returnerade {len(df_pubmed)} artiklar")
    
    # Exempel på ORCID-matchning
    orcid_client = OrcidClient()
    researcher = orcid_client.match_researcher("John Smith", keywords=["physics", "quantum"], institution="MIT")
    
    if researcher:
        print(f"Matchade forskare till ORCID: {researcher.get('orcid')}")
        print(f"Namn: {researcher.get('name')}")
        print(f"Institution: {', '.join(researcher.get('institution', []))}")
        print(f"Konfidens: {researcher.get('match_confidence')}")
    else:
        print("Ingen forskare matchades") 