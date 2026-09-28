"""DingTalk AI cards, following the official OpenClaw connector lifecycle.

Reference: DingTalk-Real-AI/dingtalk-openclaw-connector, services/messaging/card.ts.
Ordinary messages and channel_message media remain independent of this module.
"""
import asyncio
from dataclasses import dataclass, field
import json
import re
import time
import uuid

from deep_agent.channels.outbound import DeliveryError

TEMPLATE_ID = "02fcf2f4-5e02-4a85-b672-46d1f715543e.schema"


def normalize_content(content):
    """Preserve Markdown blocks while rendering ordinary line breaks in cards."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output, fenced = [], False
    block = re.compile(r"^\s{0,3}(?:[-*+] |\d+[.)] |\||#{1,6} |>|[-*_]{3,})")
    for i, line in enumerate(lines):
        if re.match(r"^\s{0,3}```", line):
            fenced = not fenced
        output.append(line)
        if i < len(lines) - 1:
            nxt = lines[i + 1]
            output.append("\n" if fenced or not line or not nxt or block.match(nxt)
                          or re.match(r"^\s{0,3}```", nxt) or re.match(r"^\s{0,3}```", line) else "<br>")
    return "".join(output)


def params(content, status):
    return {"flowStatus": status, "msgContent": normalize_content(content),
            "staticMsgContent": "", "sys_full_json_obj": json.dumps({"order": ["msgContent"]}),
            "config": json.dumps({"autoLayout": True})}


@dataclass
class Card:
    track_id: str
    delivered: bool = False
    started: bool = False
    failed_creation: bool = False
    finished: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class StreamingCards:
    def __init__(self, api, robot_code):
        self.api, self.robot_code = api, robot_code
        self.cards = {}
        self.lock = asyncio.Lock()
        self.last_request = 0.

    async def request(self, method, path, body):
        # All card operations for this application share a conservative 20 QPS limit.
        async with self.lock:
            for attempt in range(2):
                await asyncio.sleep(max(0, .05 - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                try:
                    return await self.api.request(method, path, json=body)
                except DeliveryError as exc:
                    if exc.code != "card_rate_limited" or attempt:
                        raise
                    await asyncio.sleep(2)

    async def update(self, reply_id, target, content, *, finish=False):
        card = self.cards.get(reply_id)
        if card is None:
            card = self.cards[reply_id] = Card("card_" + uuid.uuid4().hex)
        # Serialize the complete lifecycle, not only individual HTTP requests:
        # otherwise a concurrent final can overtake create/deliver or a preview.
        async with card.lock:
            if card.finished:
                raise DeliveryError("card_finalized", "卡片回复已结束")
            try:
                await self._update(card, target, content, finish=finish)
            finally:
                if finish:
                    card.finished = True
                    self.cards.pop(reply_id, None)

    async def _update(self, card, target, content, *, finish):
        if not card.delivered and not card.failed_creation:
            try:
                await self.request("POST", "/v1.0/card/instances", {
                    "cardTemplateId": TEMPLATE_ID, "outTrackId": card.track_id,
                    "cardData": {"cardParamMap": params(content or "正在处理…", "1")},
                    "callbackType": "STREAM", "imGroupOpenSpaceModel": {"supportForward": True},
                    "imRobotOpenSpaceModel": {"supportForward": True}})
                body = {"outTrackId": card.track_id, "userIdType": 1}
                if target.group:
                    body.update(openSpaceId="dtv1.card//IM_GROUP." + target.conversation_id,
                                imGroupOpenDeliverModel={"robotCode": self.robot_code})
                else:
                    if not target.staff_id:
                        raise DeliveryError("missing_staff_id", "钉钉未提供员工 ID")
                    body.update(openSpaceId="dtv1.card//IM_ROBOT." + target.staff_id,
                                imRobotOpenDeliverModel={"spaceType": "IM_ROBOT",
                                  "robotCode": self.robot_code, "extension": {"dynamicSummary": "true"}})
                await self.request("POST", "/v1.0/card/instances/deliver", body)
                card.delivered = True
            except BaseException:
                # Never create a second card after an uncertain create/deliver.
                card.failed_creation = True
                raise
        if card.failed_creation:
            raise DeliveryError("card_creation_failed", "卡片创建未完成")
        if not card.started:
            await self.request("PUT", "/v1.0/card/instances", {
                "outTrackId": card.track_id, "cardData": {"cardParamMap": params(content, "2")}})
            card.started = True
        await self.request("PUT", "/v1.0/card/streaming", {
            "outTrackId": card.track_id, "guid": uuid.uuid4().hex,
            "key": "msgContent", "content": normalize_content(content),
            "isFull": True, "isFinalize": finish, "isError": False})
        if finish:
            # Final full state must include content/order/layout, not just flowStatus.
            await self.request("PUT", "/v1.0/card/instances", {
                "outTrackId": card.track_id, "cardData": {"cardParamMap": params(content, "3")},
                "cardUpdateOptions": {"updateCardDataByKey": True}})
