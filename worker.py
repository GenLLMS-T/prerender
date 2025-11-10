import asyncio
import hashlib
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from aiobotocore.session import get_session
from utils import is_safe_url
import config

# Global queue for render tasks
render_task_queue = asyncio.Queue()


# Log errors to file
async def log_error(url: str, message: str, console_logs=None):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("failed_urls.txt", "a", encoding="utf-8") as f:
        f.write(url + "\n")
    with open("errors.log", "a", encoding="utf-8") as f:
        f.write(f"[{t}] {url} - {message}\n")
        if console_logs:
            for line in console_logs[-5:]:
                f.write(f"    {line}\n")
            f.write("\n")


# Worker maintains persistent browser context and S3 client, only creates/closes pages per request
async def worker():
    session = get_session()

    async with session.create_client(
        "s3",
        region_name=config.S3_REGION,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        endpoint_url=None,
        use_ssl=config.S3_USE_SSL
    ) as s3:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            while True:
                url = await render_task_queue.get()
                console_logs = []
                page = None

                # Validate URL (defense in depth)
                if not is_safe_url(url):
                    await log_error(url, "Invalid URL: SSRF protection triggered")
                    render_task_queue.task_done()
                    continue

                try:
                    page = await context.new_page()
                    page.on("console", lambda msg: console_logs.append(f"[console:{msg.type}] {msg.text}"))

                    await page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
                    await page.wait_for_selector(
                        "meta[data-gen-source='meta-loader']",
                        state="attached",
                        timeout=config.META_LOADER_TIMEOUT
                    )

                    html = await page.content()

                    # Save to S3 with MD5(url) as filename
                    key = f"{config.S3_PREFIX}/" + hashlib.md5(url.encode()).hexdigest() + ".html"
                    await s3.put_object(
                        Bucket=config.S3_BUCKET,
                        Key=key,
                        Body=html.encode("utf-8"),
                        ContentType="text/html"
                    )

                except PlaywrightTimeoutError:
                    await log_error(url, "timeout", console_logs)
                except Exception as e:
                    await log_error(url, f"{type(e).__name__}: {e}", console_logs)
                finally:
                    if page:
                        await page.close()
                    render_task_queue.task_done()


# Main entry point to start workers
async def start_workers(num_workers: int = 10):
    workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
    await asyncio.gather(*workers)
