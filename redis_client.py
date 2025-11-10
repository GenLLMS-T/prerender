import redis.asyncio as redis
import config


async def create_redis_client():
    # Create and return a Redis client connection
    client = await redis.from_url(
        config.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_keepalive=True
    )
    return client


async def close_redis_client(client):
    # Close Redis client connection
    await client.aclose()
