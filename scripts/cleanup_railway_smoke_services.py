from __future__ import annotations

import json
import os
import urllib.request

API = "https://backboard.railway.com/graphql/v2"
ENVIRONMENT_ID = os.environ["RAILWAY_ENVIRONMENT_ID"]
TOKEN = os.environ["RAILWAY_TOKEN"]
SERVICE_IDS = [
    "f59f2193-0c38-456c-87e8-c909281a0521",
    "f5bc3206-bdc9-486e-a09d-d347a0f3f66b",
    "99be212d-e723-48b3-aa4e-c57cbc602095",
    "073d27b6-962f-4202-a834-f37c60d90503",
    "d51e17f5-8299-4922-9eeb-69cd05fd4a77",
    "6729898f-017e-47c0-8dfc-d18feec2081b",
    "455cccfe-8ef7-4219-a9a9-4762129ced60",
    "e84d96ad-c43c-42ff-aee5-a8ab1540c286",
    "5cd85e4b-ea02-4a76-a95f-7c40da9b8542",
]
QUERY = "mutation DeleteService($id:String!,$environmentId:String!){serviceDelete(id:$id,environmentId:$environmentId)}"

for service_id in SERVICE_IDS:
    try:
        payload = json.dumps({"query": QUERY, "variables": {"id": service_id, "environmentId": ENVIRONMENT_ID}}).encode()
        request = urllib.request.Request(
            API,
            data=payload,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "User-Agent": "Operly-Smoke-Cleanup/1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode())
        if body.get("errors") or body.get("data", {}).get("serviceDelete") is not True:
            raise RuntimeError(json.dumps(body, separators=(",", ":")))
        print(f"DELETED {service_id}", flush=True)
    except Exception as error:
        print(f"DELETE_FAILED {service_id} {type(error).__name__}: {error}", flush=True)
