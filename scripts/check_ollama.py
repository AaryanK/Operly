"""Safe Ollama connectivity check. Never prints credentials or prompt data."""

import asyncio
import os

from dotenv import load_dotenv

from packages.business_brain.ollama_client import OllamaClient, OllamaError


async def main() -> int:
    load_dotenv()
    model = os.getenv("OLLAMA_MODEL", "").strip() or "(unset)"
    fallback = os.getenv("OLLAMA_FALLBACK_MODEL", "").strip() or "(none)"
    print(f"Primary model: {model}")
    print(f"Fallback model: {fallback}")

    try:
        message = await OllamaClient().chat(
            [{"role": "user", "content": "Reply with exactly OK."}],
            [],
        )
    except OllamaError as error:
        print(error.public_message)
        return 1
    except RuntimeError as error:
        print(str(error))
        return 1

    print("Ollama response received:", str(message.get("content", ""))[:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
