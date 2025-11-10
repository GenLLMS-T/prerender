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


async def log_error(url: str, message: str, console_logs=None):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Log failed URL
    with open("logs/failed_urls.txt", "a", encoding="utf-8") as f:
        f.write(url + "\n")
    # Log detailed error with console output
    with open("logs/errors.log", "a", encoding="utf-8") as f:
        f.write(f"[{t}] {url} - {message}\n")
        if console_logs:
            for line in console_logs[-5:]:
                f.write(f"    {line}\n")
            f.write("\n")


async def render_page(url: str, browser_context: BrowserContext, s3_client) -> str:
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
            await log_error(url, "meta tag timeout (partial render)", console_logs)

        # Get rendered HTML (even if partial)
        html = await page.content()

        # Only cache complete renders to S3
        if is_complete:
            key = f"{config.S3_PREFIX}/" + hashlib.md5(url.encode()).hexdigest() + ".html"
            await s3_client.put_object(
                Bucket=config.S3_BUCKET,
                Key=key,
                Body=html.encode("utf-8"),
                ContentType="text/html"
            )

        return html

    except PlaywrightTimeoutError:
        # Page load timeout - complete failure
        await log_error(url, "page load timeout", console_logs)
        raise
    except Exception as e:
        await log_error(url, f"{type(e).__name__}: {e}", console_logs)
        raise
    finally:
        if page:
            await page.close()
