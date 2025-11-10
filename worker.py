import asyncio
import hashlib
import os
from datetime import datetime
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeoutError
from aiobotocore.session import get_session
from utils import is_safe_url
import config

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)


async def log_render(url: str, status: str, message: str = "", console_logs=None):
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")

    # Console output
    print(f"[{timestamp}] [{status.upper()}] {url}")
    if message:
        print(f"  → {message}")

    # Log to daily file
    log_file = f"logs/render-{date_str}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [{status.upper()}] {url}\n")
        if message:
            f.write(f"  → {message}\n")
        if console_logs:
            for line in console_logs[-5:]:
                f.write(f"    {line}\n")
        f.write("\n")

    # Track failed URLs (with deduplication)
    if status == "failed":
        # Read existing URLs
        failed_set = set()
        failed_file = "logs/failed_urls.txt"
        if os.path.exists(failed_file):
            with open(failed_file, "r", encoding="utf-8") as f:
                failed_set = set(line.strip() for line in f if line.strip())

        # Add new URL if not exists
        if url not in failed_set:
            with open(failed_file, "a", encoding="utf-8") as f:
                f.write(url + "\n")


async def render_page(url: str, browser_context: BrowserContext, s3_client, redis_client, url_hash: str) -> str:
    console_logs = []
    page = None
    is_complete = False

    try:
        page = await browser_context.new_page()
        page.on("console", lambda msg: console_logs.append(f"[console:{msg.type}] {msg.text}"))

        # Load page
        await page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)

        # Wait for meta tag (indicates rendering complete)
        try:
            await page.wait_for_selector(
                "meta[data-gen-source='meta-loader']",
                state="attached",
                timeout=config.META_LOADER_TIMEOUT
            )
            is_complete = True
        except PlaywrightTimeoutError:
            # Meta tag not found - partial render, but continue
            await log_render(url, "partial", "meta tag timeout (partial render)", console_logs)

        # Get rendered HTML (even if partial)
        html = await page.content()

        # Only cache complete renders to S3 and Redis
        if is_complete:
            # Save to S3
            s3_key = f"{config.S3_PREFIX}/{url_hash}.html"
            await s3_client.put_object(
                Bucket=config.S3_BUCKET,
                Key=s3_key,
                Body=html.encode("utf-8"),
                ContentType="text/html"
            )

            # Save to Redis (TTL: 1 hour)
            try:
                redis_cache_key = f"render:cache:{url_hash}"
                await redis_client.setex(redis_cache_key, config.REDIS_CACHE_TTL, html)
            except Exception as e:
                print(f"Redis cache store error: {e}")

            # Log success
            await log_render(url, "success", "rendered and cached (S3 + Redis)")

        return html

    except PlaywrightTimeoutError:
        # Page load timeout - complete failure
        await log_render(url, "failed", "page load timeout", console_logs)
        raise
    except Exception as e:
        await log_render(url, "failed", f"{type(e).__name__}: {e}", console_logs)
        raise
    finally:
        if page:
            await page.close()
