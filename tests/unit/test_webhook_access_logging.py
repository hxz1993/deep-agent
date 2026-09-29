import logging
import unittest
from deep_agent.core.logging import WebhookAccessLogFilter


class WebhookAccessLoggingTests(unittest.TestCase):
    def test_webhook_query_is_scrubbed_before_formatting(self):
        record = logging.LogRecord('uvicorn.access',20,'',0,'%s - "%s %s HTTP/%s" %d',
            ('peer','GET','/api/v1/webhooks/channels/channel?echostr=PRIVATE&msg_signature=PRIVATE','1.1',200),None)
        self.assertTrue(WebhookAccessLogFilter().filter(record))
        self.assertNotIn('PRIVATE',record.getMessage())
        self.assertIn('/api/v1/webhooks/channels/channel',record.getMessage())

    def test_other_routes_are_unchanged(self):
        record = logging.LogRecord('uvicorn.access',20,'',0,'%s - "%s %s HTTP/%s" %d',
            ('peer','GET','/api/v1/sessions?limit=20','1.1',200),None)
        previous=record.getMessage()
        WebhookAccessLogFilter().filter(record)
        self.assertEqual(record.getMessage(),previous)
