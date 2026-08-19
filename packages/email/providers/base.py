from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailEnvelope:
    to_email: str
    subject: str
    html_body: str
    text_body: str


class EmailProvider(Protocol):
    async def send(self, envelope: EmailEnvelope) -> None: ...
