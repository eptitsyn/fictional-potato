import uuid
from datetime import datetime

from pydantic import BaseModel


class RequestLogRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID | None
    username: str | None
    ip_address: str | None
    method: str
    path: str
    status_code: int
    timestamp: datetime
