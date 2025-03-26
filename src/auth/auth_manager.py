from typing import Optional, Dict, Any
import logging
from flask import session, redirect, url_for, request
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from authlib.integrations.flask_client import OAuth
from config.database import AUTH_CONFIG

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AuthManager:
    """Class for managing authentication with support for SAML and OIDC."""
    
    def __init__(self, app):
        """Initialize authentication manager."""
        self.app = app
        self.auth_type = AUTH_CONFIG['type']
        self._setup_auth()
        logger.info(f"AuthManager initialized with type: {self.auth_type}")
    
    def _setup_auth(self):
        """Setup authentication based on configured type."""
        if self.auth_type == 'saml':
            self._setup_saml()
        elif self.auth_type == 'oidc':
            self._setup_oidc()
        else:
            logger.warning("No authentication type configured, using local auth")
    
    def _setup_saml(self):
        """Setup SAML authentication."""
        try:
            saml_config = AUTH_CONFIG['saml']
            self.saml_auth = OneLogin_Saml2_Auth(
                request,
                custom_base_path=None,
                sp_entity_id=saml_config['entity_id'],
                sp_assertion_consumer_service_url=saml_config['acs_url'],
                sp_single_logout_service_url=None,
                sp_x509cert=saml_config['cert_file'],
                sp_privatekey=saml_config['key_file'],
                idp_entity_id=None,
                idp_single_sign_on_service_url=None,
                idp_single_logout_service_url=None,
                idp_x509cert=None,
                security={
                    'nameIdEncrypted': True,
                    'authnRequestsSigned': True,
                    'logoutRequestSigned': True,
                    'logoutResponseSigned': True,
                    'signMetadata': True,
                    'wantAssertionsSigned': True,
                    'wantNameIdEncrypted': True,
                    'wantAssertionsEncrypted': True,
                    'wantNameId': True,
                    'wantAttributeStatement': True,
                    'requestedAuthnContext': False,
                    'requestedAuthnContextComparison': 'exact',
                    'allowRepeatAttributeName': True,
                    'allowDuplicateAttributeValues': True,
                    'rejectDeprecatedAlgorithm': True
                }
            )
            logger.info("SAML authentication configured")
        except Exception as e:
            logger.error(f"Error setting up SAML: {str(e)}")
            raise
    
    def _setup_oidc(self):
        """Setup OIDC authentication."""
        try:
            oidc_config = AUTH_CONFIG['oidc']
            oauth = OAuth(self.app)
            
            self.oidc = oauth.register(
                name='oidc',
                client_id=oidc_config['client_id'],
                client_secret=oidc_config['client_secret'],
                server_metadata_url=oidc_config['discovery_url'],
                client_kwargs={
                    'scope': 'openid email profile',
                    'redirect_uri': oidc_config['redirect_uri']
                }
            )
            logger.info("OIDC authentication configured")
        except Exception as e:
            logger.error(f"Error setting up OIDC: {str(e)}")
            raise
    
    def login(self) -> Optional[str]:
        """Initiate login process."""
        try:
            if self.auth_type == 'saml':
                return self.saml_auth.login()
            elif self.auth_type == 'oidc':
                return redirect(self.oidc.authorize_redirect())
            else:
                return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Error during login: {str(e)}")
            return None
    
    def logout(self) -> Optional[str]:
        """Initiate logout process."""
        try:
            if self.auth_type == 'saml':
                return self.saml_auth.logout()
            elif self.auth_type == 'oidc':
                return redirect(self.oidc.logout())
            else:
                session.clear()
                return redirect(url_for('logout'))
        except Exception as e:
            logger.error(f"Error during logout: {str(e)}")
            return None
    
    def process_saml_response(self) -> Optional[Dict[str, Any]]:
        """Process SAML response and extract user information."""
        try:
            if not self.saml_auth:
                return None
            
            self.saml_auth.process_response()
            if not self.saml_auth.is_authenticated():
                return None
            
            return {
                'name_id': self.saml_auth.get_nameid(),
                'attributes': self.saml_auth.get_attributes(),
                'session_index': self.saml_auth.get_session_index()
            }
        except Exception as e:
            logger.error(f"Error processing SAML response: {str(e)}")
            return None
    
    def process_oidc_response(self) -> Optional[Dict[str, Any]]:
        """Process OIDC response and extract user information."""
        try:
            if not self.oidc:
                return None
            
            token = self.oidc.authorize_access_token()
            userinfo = self.oidc.parse_id_token(token)
            
            return {
                'sub': userinfo.get('sub'),
                'email': userinfo.get('email'),
                'name': userinfo.get('name'),
                'preferred_username': userinfo.get('preferred_username')
            }
        except Exception as e:
            logger.error(f"Error processing OIDC response: {str(e)}")
            return None
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Get current user information from session."""
        return session.get('user')
    
    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return 'user' in session
    
    def require_auth(self, f):
        """Decorator to require authentication for routes."""
        def decorated_function(*args, **kwargs):
            if not self.is_authenticated():
                return self.login()
            return f(*args, **kwargs)
        return decorated_function 