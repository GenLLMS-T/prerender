import asyncio
import hashlib
from fastapi import HTTPException
from worker import render_page
import config


async def render_url_service(
    url: str,
    cache_s3_client,
    browser_pool: asyncio.Queue,
    s3_pool: asyncio.Queue,
    render_semaphore: asyncio.Semaphore
) -> str:
    # Generate S3 cache key
    key = f"{config.S3_PREFIX}/" + hashlib.md5(url.encode()).hexdigest() + ".html"

    # Check S3 cache first
    try:
        await cache_s3_client.head_object(Bucket=config.S3_BUCKET, Key=key)
        obj = await cache_s3_client.get_object(Bucket=config.S3_BUCKET, Key=key)
        async with obj["Body"] as stream:
            body = await stream.read()
        return body.decode("utf-8")
    except cache_s3_client.exceptions.NoSuchKey:
        pass
    except Exception as e:
        print(f"S3 cache check error: {e}")

    # Acquire semaphore to limit concurrent renders
    async with render_semaphore:
        # Double-check cache (another request might have rendered it)
        try:
            await cache_s3_client.head_object(Bucket=config.S3_BUCKET, Key=key)
            obj = await cache_s3_client.get_object(Bucket=config.S3_BUCKET, Key=key)
            async with obj["Body"] as stream:
                body = await stream.read()
            return body.decode("utf-8")
        except cache_s3_client.exceptions.NoSuchKey:
            pass
        except Exception:
            pass

        # Get resources from pools
        browser_context = await browser_pool.get()
        s3_async = await s3_pool.get()

        try:
            html = await render_page(url, browser_context, s3_async)
            return html
        except Exception as e:
            raise HTTPException(500, f"Rendering failed: {type(e).__name__}: {str(e)}")
        finally:
            # Always return resources to pool
            await browser_pool.put(browser_context)
            await s3_pool.put(s3_async)
