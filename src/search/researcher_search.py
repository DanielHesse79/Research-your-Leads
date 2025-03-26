from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search, Document, Text, Keyword, Integer, Date, Nested
from typing import List, Dict, Optional
import logging
from config.database import ELASTICSEARCH_CONFIG

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ResearcherDocument(Document):
    """Elasticsearch document mapping for researchers."""
    
    name = Text(fields={'keyword': Keyword()})
    orcid = Keyword()
    institution = Text(fields={'keyword': Keyword()})
    email = Keyword()
    research_areas = Text(fields={'keyword': Keyword()})
    publications = Nested(
        properties={
            'title': Text(),
            'year': Integer(),
            'doi': Keyword(),
            'pmid': Keyword(),
            'authors': Text(fields={'keyword': Keyword()})
        }
    )
    grants = Nested(
        properties={
            'title': Text(),
            'year': Integer(),
            'amount': Integer(),
            'funder': Text(fields={'keyword': Keyword()})
        }
    )
    last_updated = Date()
    
    class Index:
        name = f"{ELASTICSEARCH_CONFIG['index_prefix']}_researchers"
        settings = {
            'number_of_shards': 1,
            'number_of_replicas': 1
        }

class ResearcherSearch:
    """Class for handling researcher search operations with Elasticsearch."""
    
    def __init__(self):
        """Initialize Elasticsearch client."""
        self.client = Elasticsearch(
            hosts=ELASTICSEARCH_CONFIG['hosts'],
            basic_auth=(ELASTICSEARCH_CONFIG['username'], ELASTICSEARCH_CONFIG['password'])
        )
        self._ensure_index_exists()
        logger.info("ResearcherSearch initialized")
    
    def _ensure_index_exists(self):
        """Ensure the researcher index exists."""
        if not self.client.indices.exists(index=ResearcherDocument.Index.name):
            ResearcherDocument.init()
            logger.info(f"Created index {ResearcherDocument.Index.name}")
    
    def index_researcher(self, researcher_data: Dict) -> bool:
        """Index a researcher document."""
        try:
            doc = ResearcherDocument(**researcher_data)
            doc.save()
            logger.info(f"Indexed researcher: {researcher_data.get('name', 'Unknown')}")
            return True
        except Exception as e:
            logger.error(f"Error indexing researcher: {str(e)}")
            return False
    
    def bulk_index_researchers(self, researchers: List[Dict]) -> bool:
        """Index multiple researcher documents in bulk."""
        try:
            operations = []
            for researcher in researchers:
                operations.extend([
                    {'index': {'_index': ResearcherDocument.Index.name}},
                    researcher
                ])
            self.client.bulk(operations=operations)
            logger.info(f"Bulk indexed {len(researchers)} researchers")
            return True
        except Exception as e:
            logger.error(f"Error bulk indexing researchers: {str(e)}")
            return False
    
    def search_researchers(
        self,
        query: str,
        institution: Optional[str] = None,
        research_area: Optional[str] = None,
        min_publications: Optional[int] = None,
        page: int = 1,
        size: int = 10
    ) -> Dict:
        """Search for researchers with various filters."""
        try:
            s = Search(index=ResearcherDocument.Index.name)
            
            # Build query
            if query:
                s = s.query('multi_match', query=query, fields=['name^3', 'research_areas^2', 'institution'])
            
            if institution:
                s = s.filter('term', institution=institution)
            
            if research_area:
                s = s.filter('term', research_areas=research_area)
            
            if min_publications:
                s = s.filter('script', script={
                    'source': f"doc['publications'].length >= {min_publications}"
                })
            
            # Add pagination
            s = s[(page - 1) * size:page * size]
            
            # Execute search
            response = s.execute()
            
            return {
                'total': response.hits.total.value,
                'page': page,
                'size': size,
                'results': [hit.to_dict() for hit in response.hits]
            }
        except Exception as e:
            logger.error(f"Error searching researchers: {str(e)}")
            return {'total': 0, 'page': page, 'size': size, 'results': []}
    
    def get_researcher(self, orcid: str) -> Optional[Dict]:
        """Get a researcher by ORCID."""
        try:
            s = Search(index=ResearcherDocument.Index.name)
            s = s.filter('term', orcid=orcid)
            response = s.execute()
            
            if response.hits.total.value > 0:
                return response.hits[0].to_dict()
            return None
        except Exception as e:
            logger.error(f"Error getting researcher by ORCID: {str(e)}")
            return None
    
    def update_researcher(self, orcid: str, update_data: Dict) -> bool:
        """Update a researcher document."""
        try:
            doc = ResearcherDocument.get(id=orcid)
            for key, value in update_data.items():
                setattr(doc, key, value)
            doc.save()
            logger.info(f"Updated researcher: {orcid}")
            return True
        except Exception as e:
            logger.error(f"Error updating researcher: {str(e)}")
            return False
    
    def delete_researcher(self, orcid: str) -> bool:
        """Delete a researcher document."""
        try:
            ResearcherDocument.get(id=orcid).delete()
            logger.info(f"Deleted researcher: {orcid}")
            return True
        except Exception as e:
            logger.error(f"Error deleting researcher: {str(e)}")
            return False 