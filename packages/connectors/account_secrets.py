import json

from cryptography.fernet import InvalidToken

from packages.connectors.secrets import _fernet
from packages.database.account_connector_models import AccountConnectorSecret


async def store_account_secret(db, user_id: str, payload: dict) -> str:
    row = AccountConnectorSecret(
        user_id=user_id,
        ciphertext=_fernet().encrypt(json.dumps(payload).encode()).decode(),
    )
    db.add(row)
    await db.flush()
    return row.id


async def read_account_secret(db, user_id: str, reference: str) -> dict:
    row = await db.get(AccountConnectorSecret, reference)
    if not row or row.user_id != user_id:
        raise LookupError("Account connector credential not found")
    try:
        return json.loads(_fernet().decrypt(row.ciphertext.encode()).decode())
    except InvalidToken as error:
        raise RuntimeError("Account connector credential cannot be decrypted") from error


async def update_account_secret(db, user_id: str, reference: str, payload: dict) -> None:
    row = await db.get(AccountConnectorSecret, reference)
    if not row or row.user_id != user_id:
        raise LookupError("Account connector credential not found")
    row.ciphertext = _fernet().encrypt(json.dumps(payload).encode()).decode()
