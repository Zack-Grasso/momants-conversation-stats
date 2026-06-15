from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[str, set[WebSocket]] = {}

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(channel, set()).add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        if channel in self.active:
            self.active[channel].discard(websocket)
            if not self.active[channel]:
                del self.active[channel]

    async def broadcast(self, channel: str, message: str) -> None:
        dead: list[WebSocket] = []
        for websocket in self.active.get(channel, set()):
            try:
                await websocket.send_text(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(channel, websocket)


manager = ConnectionManager()
