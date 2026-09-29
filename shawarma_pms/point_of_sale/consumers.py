"""PHASE 2 - not imported anywhere yet. Requires: pip install channels channels-redis daphne."""
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .signals import CASHIER_GROUP, KDS_GROUP


class OrderEventsConsumer(AsyncJsonWebsocketConsumer):
    """One consumer, two audiences. Same permission checks as the Phase 1 HTTP views."""

    async def connect(self):
        user = self.scope["user"]
        if user.is_anonymous:
            return await self.close()
        self.groups_joined = []
        if await self._has_perm(user, "point_of_sale.view_kds"):
            self.groups_joined.append(KDS_GROUP)
        if await self._has_perm(user, "point_of_sale.view_ready_alerts"):
            self.groups_joined.append(CASHIER_GROUP)
        if not self.groups_joined:
            return await self.close()
        for g in self.groups_joined:
            await self.channel_layer.group_add(g, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        for g in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(g, self.channel_name)

    async def order_event(self, message):           # handler for {"type": "order.event"}
        await self.send_json(message["payload"])    # identical shape to Phase 1 `events[]`

    @staticmethod
    async def _has_perm(user, perm):
        from asgiref.sync import sync_to_async
        return await sync_to_async(user.has_perm)(perm)
