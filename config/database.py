import os
from typing import Dict, Optional

# Database configuration
DATABASE_CONFIG = {
    'default': {
        'type': os.getenv('DB_TYPE', 'sqlite'),  # 'sqlite' or 'postgresql'
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '5432')),
        'database': os.getenv('DB_NAME', 'forskardatabas'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
        'schema': os.getenv('DB_SCHEMA', 'public'),
    }
}

# Elasticsearch configuration
ELASTICSEARCH_CONFIG = {
    'hosts': [os.getenv('ES_HOST', 'localhost:9200')],
    'index_prefix': os.getenv('ES_INDEX_PREFIX', 'forskare'),
    'username': os.getenv('ES_USERNAME', ''),
    'password': os.getenv('ES_PASSWORD', ''),
}

# Authentication configuration
AUTH_CONFIG = {
    'type': os.getenv('AUTH_TYPE', 'local'),  # 'local', 'saml', or 'oidc'
    'saml': {
        'entity_id': os.getenv('SAML_ENTITY_ID', ''),
        'metadata_url': os.getenv('SAML_METADATA_URL', ''),
        'acs_url': os.getenv('SAML_ACS_URL', ''),
        'cert_file': os.getenv('SAML_CERT_FILE', ''),
        'key_file': os.getenv('SAML_KEY_FILE', ''),
    },
    'oidc': {
        'client_id': os.getenv('OIDC_CLIENT_ID', ''),
        'client_secret': os.getenv('OIDC_CLIENT_SECRET', ''),
        'discovery_url': os.getenv('OIDC_DISCOVERY_URL', ''),
        'redirect_uri': os.getenv('OIDC_REDIRECT_URI', ''),
    }
}

# Notification configuration
NOTIFICATION_CONFIG = {
    'email': {
        'enabled': os.getenv('EMAIL_NOTIFICATIONS', 'false').lower() == 'true',
        'smtp_host': os.getenv('SMTP_HOST', ''),
        'smtp_port': int(os.getenv('SMTP_PORT', '587')),
        'smtp_user': os.getenv('SMTP_USER', ''),
        'smtp_password': os.getenv('SMTP_PASSWORD', ''),
        'from_email': os.getenv('FROM_EMAIL', ''),
    },
    'slack': {
        'enabled': os.getenv('SLACK_NOTIFICATIONS', 'false').lower() == 'true',
        'webhook_url': os.getenv('SLACK_WEBHOOK_URL', ''),
        'channel': os.getenv('SLACK_CHANNEL', '#forskare-updates'),
    }
}

def get_database_url(db_type: str = 'default') -> str:
    """Get the database URL based on configuration."""
    config = DATABASE_CONFIG[db_type]
    
    if config['type'] == 'sqlite':
        return f"sqlite:///./data/{config['database']}.db"
    elif config['type'] == 'postgresql':
        return f"postgresql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"
    else:
        raise ValueError(f"Unsupported database type: {config['type']}") 