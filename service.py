import asyncio
import hashlib
from fastapi import HTTPException
from worker import render_page
import config


async def render_url_service(
    url: str,
    redis_client,
    cache_s3_client,
    browser_pool: asyncio.Queue,
    s3_pool: asyncio.Queue,
    render_semaphore: asyncio.Semaphore
) -> str:
    # Generate cache key (MD5 hash of URL)
    url_hash = hashlib.md5(url.encode()).hexdigest()
    redis_cache_key = f"render:cache:{url_hash}"
    redis_lock_key = f"render:lock:{url_hash}"
    redis_result_key = f"render:result:{url_hash}"
    s3_key = f"{config.S3_PREFIX}/{url_hash}.html"

    # Step 1: Check Redis failure cache (prevent retry storms)
    try:
        failure_key = f"render:failure:{url_hash}"
        is_failed = await redis_client.get(failure_key)
        if is_failed:
            print(f"[FAILURE CACHED] {url} - skipping render (failed recently)")
            raise HTTPException(500, "Rendering failed recently (cached)")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Redis failure check error: {e}")

    # Step 2: Check Redis cache (complete renders only, TTL: 1 hour)
    try:
        cached_html = await redis_client.get(redis_cache_key)
        if cached_html:
            print(f"[CACHE HIT: Redis] {url}")
            return cached_html
    except Exception as e:
        print(f"Redis cache check error: {e}")

    # Step 3: Check S3 cache
    try:
        await cache_s3_client.head_object(Bucket=config.S3_BUCKET, Key=s3_key)
        obj = await cache_s3_client.get_object(Bucket=config.S3_BUCKET, Key=s3_key)
        async with obj["Body"] as stream:
            body = await stream.read()
        html = body.decode("utf-8")
        print(f"[CACHE HIT: S3] {url}")

        # Store in Redis for faster future access
        try:
            await redis_client.setex(redis_cache_key, config.REDIS_CACHE_TTL, html)
        except Exception as e:
            print(f"Redis cache store error: {e}")

        return html
    except cache_s3_client.exceptions.NoSuchKey:
        pass
    except Exception as e:
        print(f"S3 cache check error: {e}")

    # Step 4: Render (with duplicate request handling)
    # Check if another request is already rendering this URL
    try:
        # Try to acquire lock (set with NX flag, TTL: 60 seconds)
        lock_acquired = await redis_client.set(redis_lock_key, "1", nx=True, ex=60)

        if not lock_acquired:
            # Another request is rendering - wait for result
            print(f"[WAITING] Another request is rendering: {url}")
            for _ in range(60):  # Wait up to 60 seconds
                await asyncio.sleep(1)
                result = await redis_client.get(redis_result_key)
                if result:
                    print(f"[CACHE HIT: Duplicate] {url}")
                    return result
            # Timeout - proceed with our own render
            print(f"[TIMEOUT] Waiting for duplicate request timed out: {url}")
    except Exception as e:
        print(f"Redis lock error: {e}")

    # Acquire semaphore to limit concurrent renders
    async with render_semaphore:
        # Double-check Redis cache (might have been rendered while waiting)
        try:
            cached_html = await redis_client.get(redis_cache_key)
            if cached_html:
                await redis_client.delete(redis_lock_key)
                return cached_html
        except Exception:
            pass

        # Get resources from pools
        browser_context = await browser_pool.get()
        s3_async = await s3_pool.get()

        try:
            # Render the page
            html = await render_page(url, browser_context, s3_async, redis_client, url_hash)

            # Store result for duplicate requests (TTL: 60 seconds)
            try:
                await redis_client.setex(redis_result_key, config.REDIS_RENDER_TTL, html)
            except Exception as e:
                print(f"Redis result store error: {e}")

            return html
        except Exception as e:
            # Store failure in Redis to prevent retry storms (TTL: 5 minutes)
            try:
                await redis_client.setex(f"render:failure:{url_hash}", config.REDIS_FAILURE_TTL, "failed")
            except Exception:
                pass
            raise HTTPException(500, f"Rendering failed: {type(e).__name__}: {str(e)}")
        finally:
            # Release lock
            try:
                await redis_client.delete(redis_lock_key)
            except Exception:
                pass

            # Always return resources to pool
            await browser_pool.put(browser_context)
            await s3_pool.put(s3_async)
