"""Start the OPERLY web service using Railway's injected port."""
import os

import uvicorn


def port() -> int:
    value=os.getenv("PORT", "8000").strip()
    try:
        result=int(value)
    except ValueError as error:
        raise RuntimeError("PORT must be an integer") from error
    if not 1 <= result <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return result


if __name__ == "__main__":
    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=port())
