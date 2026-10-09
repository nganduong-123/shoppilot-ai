from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.channels.base import InboundMessage
from app.channels.meta import MetaMessengerAdapter
from app.config import settings
from app.inbox import inbox_service
from app.integrations import commerce_connector
from app.repository import Repository, repository
from app.token_crypto import decrypt_secret

logger = logging.getLogger("shoppilot.jobs")


class JobWorker:
    def __init__(self, repo: Repository = repository, poll_seconds: float = 0.5) -> None:
        self.repo = repo
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()

    def enqueue_meta_message(
        self,
        shop_id: int,
        connection_id: int,
        event: InboundMessage,
    ) -> bool:
        return self.repo.enqueue_job(
            "meta_message",
            {
                "shop_id": shop_id,
                "connection_id": connection_id,
                "event": asdict(event),
            },
            dedupe_key=f"meta:{event.external_event_id}",
        )

    async def run(self) -> None:
        self._stop = asyncio.Event()
        stale_before = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        self.repo.requeue_stale_jobs(stale_before)
        while not self._stop.is_set():
            if await self.run_once():
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> bool:
        job = self.repo.claim_job()
        if not job:
            return False
        try:
            await self._process(job["kind"], job["payload"])
        except Exception as exc:
            logger.exception("Job %s failed", job["id"])
            self.repo.fail_job(
                job["id"], str(exc), job["attempts"], job["max_attempts"]
            )
        else:
            self.repo.complete_job(job["id"])
        return True

    async def _process(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "commerce_order":
            await commerce_connector.send_confirmed_order(payload)
            return
        if kind != "meta_message":
            raise ValueError(f"Unsupported job kind: {kind}")
        shop = self.repo.get_shop_by_id(payload["shop_id"])
        connection = self.repo.get_channel_connection(payload["connection_id"])
        if not shop or not connection:
            raise ValueError("Messenger shop or connection no longer exists")

        token = None
        encrypted = connection["config"].get("page_access_token_enc")
        if encrypted:
            token = decrypt_secret(encrypted)
        elif connection["external_account_id"] == settings.meta_page_id:
            token = settings.meta_page_access_token
        if not token:
            raise ValueError("Messenger Page token is unavailable")

        event = InboundMessage(**payload["event"])
        adapter = MetaMessengerAdapter(
            page_id=connection["external_account_id"],
            page_access_token=token,
        )
        await inbox_service.process(shop, event, adapter, connection["id"])

    async def shutdown(self, task: asyncio.Task[None]) -> None:
        for _ in range(20):
            if not await self.run_once():
                break
        self._stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()


job_worker = JobWorker()
