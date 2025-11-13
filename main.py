from fastapi import FastAPI, HTTPException, Body, UploadFile, File
from fastapi.responses import Response, RedirectResponse
from contextlib import asynccontextmanager
import asyncio
from playwright.async_api import async_playwright
from aiobotocore.session import get_session
from service import render_url_service, render_url_live_service
from utils import is_safe_url
from redis_client import create_redis_client, close_redis_client
from batch import parse_sitemap, parse_url_list, process_batch_job, generate_job_id
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


@app.get("/live")
async def render_url_live(url: str):
    # Render without any caching (always fresh)
    # Validate URL to prevent SSRF attacks
    if not is_safe_url(url):
        raise HTTPException(400, "Invalid URL: Only public HTTP(S) URLs are allowed")

    try:
        # Delegate to live service (no caching)
        html = await render_url_live_service(
            url=url,
            browser_pool=browser_pool,
            render_semaphore=render_semaphore
        )
        return Response(content=html, media_type="text/html")
    except HTTPException:
        # Rendering failed - redirect to original URL
        print(f"[{url}] [REDIRECT] → live rendering failed, redirecting to original")
        return RedirectResponse(url=url, status_code=302)


@app.post("/batch/sitemap")
async def batch_sitemap(sitemap_url: str = Body(..., embed=True)):
    # Start batch rendering from sitemap.xml
    # Validate sitemap URL
    if not is_safe_url(sitemap_url):
        raise HTTPException(400, "Invalid sitemap URL")

    # Parse sitemap
    urls = await parse_sitemap(sitemap_url)
    if not urls:
        raise HTTPException(400, "No URLs found in sitemap or failed to parse")

    # Generate job ID
    job_id = generate_job_id()

    # Create wrapper function for render_url_service
    async def render_wrapper(url: str):
        return await render_url_service(
            url=url,
            redis_client=redis_client,
            cache_s3_client=cache_s3_client,
            browser_pool=browser_pool,
            s3_pool=s3_pool,
            render_semaphore=render_semaphore
        )

    # Start background task
    asyncio.create_task(
        process_batch_job(job_id, urls, cache_s3_client, render_wrapper)
    )

    return {
        "status": "started",
        "job_id": job_id,
        "total_urls": len(urls),
        "message": f"Batch job started. Check progress at GET /batch/status/{job_id}"
    }


@app.post("/batch/file")
async def batch_file(file: UploadFile = File(...)):
    # Start batch rendering from uploaded file (newline-separated URLs)
    # Read file content
    content = await file.read()
    urls_text = content.decode("utf-8")

    # Parse URL list
    url_list = await parse_url_list(urls_text)
    if not url_list:
        raise HTTPException(400, "No valid URLs found in file")

    # Generate job ID
    job_id = generate_job_id()

    # Create wrapper function for render_url_service
    async def render_wrapper(url: str):
        return await render_url_service(
            url=url,
            redis_client=redis_client,
            cache_s3_client=cache_s3_client,
            browser_pool=browser_pool,
            s3_pool=s3_pool,
            render_semaphore=render_semaphore
        )

    # Start background task
    asyncio.create_task(
        process_batch_job(job_id, url_list, cache_s3_client, render_wrapper)
    )

    return {
        "status": "started",
        "job_id": job_id,
        "total_urls": len(url_list),
        "message": f"Batch job started. Check progress at GET /batch/status/{job_id}"
    }


@app.get("/batch/status/{job_id}")
async def batch_status(job_id: str):
    # Get batch job status
    # Get job status from S3
    import json
    s3_key = f"{config.S3_PREFIX}/batch/{job_id}.json"

    try:
        obj = await cache_s3_client.get_object(Bucket=config.S3_BUCKET, Key=s3_key)
        async with obj["Body"] as stream:
            body = await stream.read()
        job_data = json.loads(body.decode("utf-8"))

        return {
            "job_id": job_id,
            "status": job_data.get("status", "unknown"),
            "total": job_data.get("total", 0),
            "completed": job_data.get("completed", 0),
            "failed": job_data.get("failed", 0),
            "progress": f"{job_data.get('completed', 0)}/{job_data.get('total', 0)}",
            "started_at": job_data.get("started_at"),
            "completed_at": job_data.get("completed_at")
        }
    except cache_s3_client.exceptions.NoSuchKey:
        raise HTTPException(404, "Job not found")
    except Exception as e:
        print(f"[{job_id}] [S3 ERROR] → failed to get job status: {e}")
        raise HTTPException(500, f"Failed to get job status: {e}")
