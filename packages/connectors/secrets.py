import json,os
from cryptography.fernet import Fernet,InvalidToken
from packages.database.connector_models import ConnectorSecret

def _fernet():
 key=os.getenv("OPERLY_CONNECTOR_SECRET_KEY","").strip()
 if not key:raise RuntimeError("OPERLY_CONNECTOR_SECRET_KEY is missing")
 return Fernet(key.encode())
async def store_secret(db,tenant_id,payload):
 row=ConnectorSecret(tenant_id=tenant_id,ciphertext=_fernet().encrypt(json.dumps(payload).encode()).decode());db.add(row);await db.flush();return row.id
async def read_secret(db,tenant_id,reference):
 row=await db.get(ConnectorSecret,reference)
 if not row or row.tenant_id!=tenant_id:raise LookupError("Connector credential not found")
 try:return json.loads(_fernet().decrypt(row.ciphertext.encode()).decode())
 except InvalidToken as e:raise RuntimeError("Connector credential cannot be decrypted") from e
async def update_secret(db,tenant_id,reference,payload):
 row=await db.get(ConnectorSecret,reference)
 if not row or row.tenant_id!=tenant_id:raise LookupError("Connector credential not found")
 row.ciphertext=_fernet().encrypt(json.dumps(payload).encode()).decode()
