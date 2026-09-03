from __future__ import annotations

import json
import os
import urllib.request

API = "https://backboard.railway.com/graphql/v2"
ENVIRONMENT_ID = os.environ["RAILWAY_ENVIRONMENT_ID"]
TOKEN = os.environ["RAILWAY_TOKEN"]

# Exact disposable services created during plugin/full-stack smoke testing.
# The cleanup runner deletes itself last.
SERVICE_IDS = [
    "f59f2193-0c38-456c-87e8-c909281a0521",  # Operly Plugin E2E Smoke
    "f5bc3206-bdc9-486e-a09d-d347a0f3f66b",  # Operly Hosted Broker Smoke
    "99be212d-e723-48b3-aa4e-c57cbc602095",  # temp-ignore
    "073d27b6-962f-4202-a834-f37c60d90503",  # temp-ignore-2
    "d51e17f5-8299-4922-9eeb-69cd05fd4a77",  # temp-ignore-3
    "6729898f-017e-47c0-8dfc-d18feec2081b",  # Operly Worker Fullstack Smoke Driver
    "455cccfe-8ef7-4219-a9a9-4762129ced60",  # Operly Fullstack Hosting Slot Smoke
    "e84d96ad-c43c-42ff-aee5-a8ab1540c286",  # Operly Worker Fullstack Smoke Driver 2
    "5cd85e4b-ea02-4a76-a95f-7c40da9b8542",  # Operly Hosted Broker Smoke Live (self)
]

QUERY = """
mutation DeleteService($id: String!, $environmentId: String!) {
  serviceDelete(id: $id, environmentId: $environmentId)
}
"""


def delete_service(service_id: str) -> None:
    payload = json.dumps(
        {
            "query": QUERY,
            "variables": {"id": service_id, "environmentId": ENVIRONMENT_ID},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        API,
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "Operly-Smoke-Cleanup/1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"], separators=(",", ":")))
    if body.get("data", {}).get("serviceDelete") is not True:
        raise RuntimeError(f"Railway did not confirm deletion for {service_id}: {body}")
    print(f"DELETED {service_id}", flush=True)


for service_id in SERVICE_IDS:
    try:
        delete_service(service_id)
    except Exception as error:
        print(f"DELETE_FAILED {service_id} {type(error).__name__}: {error}", flush=True)
