import asyncio
from collections import defaultdict

from fastapi.encoders import jsonable_encoder
from fastapi import WebSocket


class CognilabsAIConnectionManager:
    def __init__(self):
        self._all_connections: set[WebSocket] = set()
        self._conversation_connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, conversation_id: int | None = None):
        await websocket.accept()
        async with self._lock:
            self._all_connections.add(websocket)
            if conversation_id is not None:
                self._conversation_connections[conversation_id].add(websocket)

    async def disconnect(self, websocket: WebSocket, conversation_id: int | None = None):
        async with self._lock:
            self._all_connections.discard(websocket)
            if conversation_id is not None and conversation_id in self._conversation_connections:
                self._conversation_connections[conversation_id].discard(websocket)
                if not self._conversation_connections[conversation_id]:
                    self._conversation_connections.pop(conversation_id, None)
            else:
                for key in list(self._conversation_connections.keys()):
                    self._conversation_connections[key].discard(websocket)
                    if not self._conversation_connections[key]:
                        self._conversation_connections.pop(key, None)

    async def broadcast(self, payload: dict, conversation_id: int | None = None):
        targets = set(self._all_connections)
        if conversation_id is not None:
            targets |= self._conversation_connections.get(conversation_id, set())
        encoded_payload = jsonable_encoder(payload)
        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(encoded_payload)
            except Exception as exc:
                print(f"CognilabsAI websocket broadcast error: {exc}")
                stale.append(websocket)
        for websocket in stale:
            await self.disconnect(websocket)


manager = CognilabsAIConnectionManager()
