from __future__ import annotations

from typing import Any

from app.agent import SalesAgent, sales_agent
from app.channels.base import ChannelAdapter, InboundMessage
from app.repository import Repository, repository


class InboxService:
    def __init__(
        self,
        repo: Repository = repository,
        agent: SalesAgent = sales_agent,
    ) -> None:
        self.repo = repo
        self.agent = agent

    async def process(
        self,
        shop: dict[str, Any],
        inbound: InboundMessage,
        adapter: ChannelAdapter,
        connection_id: int | None = None,
    ) -> dict[str, Any]:
        is_new = self.repo.record_channel_event(
            shop["id"], inbound.channel, inbound.external_event_id, "message", inbound.metadata
        )
        if not is_new:
            return {"status": "duplicate", "replied": False}

        try:
            conversation = self.repo.get_or_create_channel_conversation(
                shop_id=shop["id"],
                connection_id=connection_id,
                channel=inbound.channel,
                external_conversation_id=inbound.external_conversation_id,
                external_customer_id=inbound.external_customer_id,
                customer_name=inbound.customer_name,
                metadata=inbound.metadata,
            )
            self.repo.add_channel_message(
                conversation["id"],
                external_message_id=inbound.external_event_id,
                direction="inbound",
                sender_type="customer",
                content=inbound.text,
                status="received",
                metadata=inbound.metadata,
            )
            if not conversation["bot_enabled"]:
                self.repo.update_channel_workflow(
                    conversation["id"],
                    status="waiting",
                    assigned_to=conversation.get("assigned_to"),
                    bot_enabled=False,
                )
                self.repo.add_message(
                    conversation["internal_conversation_id"],
                    "user",
                    inbound.text,
                    {"source": inbound.channel, "handled_by": "human"},
                )
                self.repo.mark_channel_event(
                    inbound.channel, inbound.external_event_id, "waiting_for_human"
                )
                return {
                    "status": "waiting_for_human",
                    "replied": False,
                    "channel_conversation_id": conversation["id"],
                }

            result = await self.agent.respond(
                shop, inbound.text, conversation["internal_conversation_id"]
            )
            if result["status"] == "handoff":
                self.repo.set_channel_bot(conversation["id"], False, None)
            delivery = await adapter.send_text(inbound.external_customer_id, result["message"])
            outbound_id = delivery.get("message_id") if isinstance(delivery, dict) else None
            self.repo.add_channel_message(
                conversation["id"],
                external_message_id=outbound_id,
                direction="outbound",
                sender_type="ai",
                content=result["message"],
                status="sent",
                metadata={"model": result["model"], "delivery": delivery},
            )
            self.repo.mark_channel_event(inbound.channel, inbound.external_event_id, "processed")
            return {
                **result,
                "status": result["status"],
                "replied": True,
                "channel_conversation_id": conversation["id"],
            }
        except Exception as exc:
            self.repo.mark_channel_event(
                inbound.channel, inbound.external_event_id, "failed", str(exc)[:500]
            )
            raise

    async def reply_as_human(
        self,
        conversation: dict[str, Any],
        text: str,
        agent_name: str,
        adapter: ChannelAdapter,
    ) -> dict[str, Any]:
        self.repo.set_channel_bot(conversation["id"], False, agent_name)
        delivery = await adapter.send_text(conversation["external_customer_id"], text)
        external_id = delivery.get("message_id") if isinstance(delivery, dict) else None
        message = self.repo.add_channel_message(
            conversation["id"],
            external_message_id=external_id,
            direction="outbound",
            sender_type="human",
            content=text,
            status="sent",
            metadata={"agent_name": agent_name, "delivery": delivery},
        )
        self.repo.add_message(
            conversation["internal_conversation_id"],
            "assistant",
            text,
            {"source": "human-agent", "agent_name": agent_name},
        )
        self.repo.update_channel_workflow(
            conversation["id"],
            status="open",
            assigned_to=agent_name,
            bot_enabled=False,
        )
        return message


inbox_service = InboxService()
