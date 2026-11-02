"""Read-only Gemini reachability check using the app's own config loading.

Prints only key presence, the configured model name, and one fixed result
line: HTTP status or exception class plus elapsed seconds. It never prints
the API key, URL, request headers or the response body. It makes ONE
model-metadata GET (not an inference/generation call) and starts nothing
else - it does not import bot.py.

Run from the repo root with the same Python that runs bot.py:
    python diag_gemini.py
"""
import asyncio
import os
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import aiohttp

# Same names, stripping and default-resolution as bot.py reads them.
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"


async def main():
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY: missing in this environment (bot would need GEMINI_API_KEY or GROQ_API_KEY)")
        return
    print("GEMINI_API_KEY: present")
    print(f"GEMINI_MODEL: {GEMINI_MODEL}")
    start = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(URL, headers={"x-goog-api-key": GEMINI_API_KEY}) as resp:
                await resp.read()  # consume the body; never print it
                print(f"models-lookup: http={resp.status} time={time.monotonic()-start:.1f}s")
    except Exception as exc:
        print(f"models-lookup: error={type(exc).__name__} time={time.monotonic()-start:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
