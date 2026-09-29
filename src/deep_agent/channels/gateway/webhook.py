"""Authenticated API-to-Gateway message envelope; never accepts platform secrets."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from deep_agent.channels.contracts import InboundMedia, InboundMessage
from deep_agent.channels.adapters.wecom_app import MediaHandle


class WebhookMedia(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['image', 'voice', 'file']
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=128)
    media_id: str = Field(min_length=1, max_length=1024)


class WebhookMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message_type: str = Field(min_length=1, max_length=32)
    message_id: str = Field(min_length=1, max_length=191)
    request_id: str | None = Field(default=None, max_length=191)
    sender_id: str = Field(min_length=1, max_length=191)
    peer_id: str = Field(min_length=1, max_length=191)
    text: str = Field(default='', max_length=65536)
    payload_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    tenant_id: str | None = Field(default=None, max_length=191)
    source_metadata: dict = Field(default_factory=dict)
    media: list[WebhookMedia] = Field(default_factory=list, max_length=1)
    expected_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')

    def message(self):
        if self.peer_id != self.sender_id:
            raise ValueError('WeCom application callbacks must be private messages')
        return InboundMessage(
            message_type=self.message_type, message_id=self.message_id,
            request_id=self.request_id, sender_id=self.sender_id, peer_type='single',
            peer_id=self.peer_id, peer_metadata={}, text=self.text,
            payload_hash=self.payload_hash, tenant_id=self.tenant_id,
            source_metadata=self.source_metadata,
            command_eligible=self.message_type == 'text',
            media=tuple(InboundMedia(item.kind, item.filename, item.mime,
                                    MediaHandle(self.message_id, item.media_id)) for item in self.media))
