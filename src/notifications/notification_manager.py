from typing import List, Dict, Optional
import logging
from flask_mail import Mail, Message
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config.database import NOTIFICATION_CONFIG

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NotificationManager:
    """Class for managing notifications via email and Slack."""
    
    def __init__(self, app):
        """Initialize notification manager."""
        self.app = app
        self._setup_notifications()
        logger.info("NotificationManager initialized")
    
    def _setup_notifications(self):
        """Setup notification services based on configuration."""
        # Setup email
        if NOTIFICATION_CONFIG['email']['enabled']:
            self.app.config.update(
                MAIL_SERVER=NOTIFICATION_CONFIG['email']['smtp_host'],
                MAIL_PORT=NOTIFICATION_CONFIG['email']['smtp_port'],
                MAIL_USE_TLS=True,
                MAIL_USERNAME=NOTIFICATION_CONFIG['email']['smtp_user'],
                MAIL_PASSWORD=NOTIFICATION_CONFIG['email']['smtp_password'],
                MAIL_DEFAULT_SENDER=NOTIFICATION_CONFIG['email']['from_email']
            )
            self.mail = Mail(self.app)
            logger.info("Email notifications configured")
        
        # Setup Slack
        if NOTIFICATION_CONFIG['slack']['enabled']:
            self.slack_client = WebClient(token=NOTIFICATION_CONFIG['slack']['webhook_url'])
            self.slack_channel = NOTIFICATION_CONFIG['slack']['channel']
            logger.info("Slack notifications configured")
    
    def send_email(
        self,
        subject: str,
        recipients: List[str],
        body: str,
        html: Optional[str] = None,
        attachments: Optional[List[Dict]] = None
    ) -> bool:
        """Send an email notification."""
        if not NOTIFICATION_CONFIG['email']['enabled']:
            logger.warning("Email notifications are disabled")
            return False
        
        try:
            msg = Message(
                subject=subject,
                recipients=recipients,
                body=body,
                html=html
            )
            
            if attachments:
                for attachment in attachments:
                    msg.attach(
                        filename=attachment['filename'],
                        content_type=attachment['content_type'],
                        data=attachment['data']
                    )
            
            self.mail.send(msg)
            logger.info(f"Email sent to {', '.join(recipients)}")
            return True
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            return False
    
    def send_slack_message(
        self,
        message: str,
        blocks: Optional[List[Dict]] = None,
        channel: Optional[str] = None
    ) -> bool:
        """Send a Slack notification."""
        if not NOTIFICATION_CONFIG['slack']['enabled']:
            logger.warning("Slack notifications are disabled")
            return False
        
        try:
            channel = channel or self.slack_channel
            response = self.slack_client.chat_postMessage(
                channel=channel,
                text=message,
                blocks=blocks
            )
            logger.info(f"Slack message sent to channel {channel}")
            return True
        except SlackApiError as e:
            logger.error(f"Error sending Slack message: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending Slack message: {str(e)}")
            return False
    
    def notify_researcher_update(
        self,
        researcher_name: str,
        update_type: str,
        details: Dict,
        recipients: Optional[List[str]] = None
    ) -> bool:
        """Send notifications about researcher updates."""
        success = True
        
        # Prepare message content
        subject = f"Researcher Update: {researcher_name}"
        message = f"Researcher {researcher_name} has been {update_type}.\n\n"
        message += "Details:\n"
        for key, value in details.items():
            message += f"- {key}: {value}\n"
        
        # Send email if recipients provided
        if recipients and NOTIFICATION_CONFIG['email']['enabled']:
            email_success = self.send_email(
                subject=subject,
                recipients=recipients,
                body=message
            )
            success = success and email_success
        
        # Send Slack notification
        if NOTIFICATION_CONFIG['slack']['enabled']:
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": subject
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message
                    }
                }
            ]
            slack_success = self.send_slack_message(
                message=message,
                blocks=blocks
            )
            success = success and slack_success
        
        return success
    
    def notify_data_import(
        self,
        dataset_name: str,
        record_count: int,
        success: bool,
        errors: Optional[List[str]] = None,
        recipients: Optional[List[str]] = None
    ) -> bool:
        """Send notifications about data import results."""
        success_status = True
        
        # Prepare message content
        status = "completed successfully" if success else "failed"
        subject = f"Data Import {status}: {dataset_name}"
        message = f"Data import for {dataset_name} has {status}.\n"
        message += f"Records processed: {record_count}\n"
        
        if errors:
            message += "\nErrors encountered:\n"
            for error in errors:
                message += f"- {error}\n"
        
        # Send email if recipients provided
        if recipients and NOTIFICATION_CONFIG['email']['enabled']:
            email_success = self.send_email(
                subject=subject,
                recipients=recipients,
                body=message
            )
            success_status = success_status and email_success
        
        # Send Slack notification
        if NOTIFICATION_CONFIG['slack']['enabled']:
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": subject
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message
                    }
                }
            ]
            if errors:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Errors:*\n" + "\n".join(f"• {error}" for error in errors)
                    }
                })
            
            slack_success = self.send_slack_message(
                message=message,
                blocks=blocks
            )
            success_status = success_status and slack_success
        
        return success_status 