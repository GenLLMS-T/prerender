from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, RedirectResponse
from contextlib import asynccontextmanager
import asyncio
from playwright.async_api import async_playwright
from aiobotocore.session import get_session
from service import render_url_service
from utils import is_safe_url
from redis_client import create_redis_client, close_redis_client
import config

cache_s3_client = None
redis_client = None
browser_pool = None
s3_pool = None
render_semaphore = None
playwright_instance = None


async def startup_resources():
    global cache_s3_client, redis_client, browser_pool, s3_pool, render_semaphore, playwright_instance

    session = get_session()

    # Initialize Redis client
    redis_client = await create_redis_client()
    print("✓ Redis client connected")

    # Initialize async S3 client for cache operations
    cache_s3_client = await session.create_client(
        "s3",
        region_name=config.S3_REGION,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        endpoint_url=None,
        use_ssl=config.S3_USE_SSL
    ).__aenter__()

    # Initialize semaphore and pools
    render_semaphore = asyncio.Semaphore(config.NUM_WORKERS)
    browser_pool = asyncio.Queue(maxsize=config.NUM_WORKERS)
    s3_pool = asyncio.Queue(maxsize=config.NUM_WORKERS)

    # Create Playwright instance and browser
    playwright_instance = await async_playwright().start()
    browser = await playwright_instance.chromium.launch(headless=True)

    # Create browser contexts and S3 clients for rendering
    for _ in range(config.NUM_WORKERS):
        context = await browser.new_context()
        await browser_pool.put(context)

        s3_async = await session.create_client(
            "s3",
            region_name=config.S3_REGION,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            endpoint_url=None,
            use_ssl=config.S3_USE_SSL
        ).__aenter__()
        await s3_pool.put(s3_async)

    print(f"✓ Initialized {config.NUM_WORKERS} browser contexts and S3 clients")
    return browser


async def cleanup_resources(browser):
    print("Shutting down...")

    # Close Redis client
    await close_redis_client(redis_client)

    # Close all browser contexts
    while not browser_pool.empty():
        context = await browser_pool.get()
        await context.close()

    # Close S3 clients
    while not s3_pool.empty():
        s3 = await s3_pool.get()
        await s3.__aexit__(None, None, None)

    # Close cache S3 client
    await cache_s3_client.__aexit__(None, None, None)

    # Close browser and Playwright
    await browser.close()
    await playwright_instance.stop()

    print("✓ Cleanup complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    browser = await startup_resources()
    yield
    await cleanup_resources(browser)


app = FastAPI(lifespan=lifespan)


@app.get("/render")
async def render_url(url: str):
    # Validate URL to prevent SSRF attacks
    if not is_safe_url(url):
        raise HTTPException(400, "Invalid URL: Only public HTTP(S) URLs are allowed")

    try:
        # Delegate to service layer
        html = await render_url_service(
            url=url,
            redis_client=redis_client,
            cache_s3_client=cache_s3_client,
            browser_pool=browser_pool,
            s3_pool=s3_pool,
            render_semaphore=render_semaphore
        )
        return Response(content=html, media_type="text/html")
    except HTTPException:
        # Rendering failed - redirect to original URL
        print(f"[{url}] [REDIRECT] → rendering failed, redirecting to original")
        return RedirectResponse(url=url, status_code=302)
