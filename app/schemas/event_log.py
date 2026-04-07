import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EventLogRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    event_type: str
    level: str
    user_id: uuid.UUID | None
    username: str | None
    ip_address: str | None
    message: str
    details: dict[str, Any] | None
    timestamp: datetime
