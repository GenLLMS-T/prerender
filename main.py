from fastapi import FastAPI
from fastapi.responses import Response
import asyncio
import hashlib
import boto3
from worker import render_task_queue, start_workers
import config

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_workers(config.NUM_WORKERS))

@app.get("/render")
async def render_url(url: str):
    key = f"{config.S3_PREFIX}/" + hashlib.md5(url.encode()).hexdigest() + ".html"

    s3 = boto3.client(
        "s3",
        region_name=config.S3_REGION,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        endpoint_url=None,
        use_ssl=config.S3_USE_SSL
    )

    try:
        s3.head_object(Bucket=config.S3_BUCKET, Key=key)
        obj = s3.get_object(Bucket=config.S3_BUCKET, Key=key)
        html_content = obj["Body"].read().decode("utf-8")
        return Response(content=html_content, media_type="text/html")

    except s3.exceptions.ClientError:
        # Cache miss - add to queue
        await render_task_queue.put(url)
        return {"status": "queued", "url": url}
