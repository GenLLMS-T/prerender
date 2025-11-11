import asyncio
import uuid
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List
import httpx


async def parse_sitemap(sitemap_url: str) -> List[str]:
    # Parse sitemap.xml and extract all URLs
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(sitemap_url)
            response.raise_for_status()

        # Parse XML
        root = ET.fromstring(response.text)

        # Extract URLs from sitemap
        # Handle both sitemap and sitemap index formats
        urls = []

        # Check for sitemap index (contains <sitemap> tags)
        namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        sitemaps = root.findall('.//ns:sitemap/ns:loc', namespaces)

        if sitemaps:
            # This is a sitemap index - recursively fetch child sitemaps
            for sitemap in sitemaps:
                child_urls = await parse_sitemap(sitemap.text)
                urls.extend(child_urls)
        else:
            # Regular sitemap - extract URLs
            url_tags = root.findall('.//ns:url/ns:loc', namespaces)
            urls = [url.text for url in url_tags if url.text]

        return urls
    except Exception as e:
        print(f"[ERROR] Failed to parse sitemap {sitemap_url}: {e}")
        return []


async def parse_url_list(text: str) -> List[str]:
    # Parse newline-separated URL list
    urls = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if line and line.startswith('http'):
            urls.append(line)
    return urls


async def save_job_status_to_s3(job_id: str, job_data: dict, s3_client, s3_bucket: str, s3_prefix: str):
    # Save job status to S3
    s3_key = f"{s3_prefix}/batch/{job_id}.json"

    try:
        await s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=json.dumps(job_data).encode("utf-8"),
            ContentType="application/json"
        )
    except Exception as e:
        print(f"[{job_id}] [S3 ERROR] → failed to save job status: {e}")


async def process_batch_job(
    job_id: str,
    urls: List[str],
    s3_client,
    render_url_func
):
    # Process batch rendering job in background
    from config import S3_BUCKET, S3_PREFIX

    total = len(urls)
    completed = 0
    failed = 0

    # Initialize job status in S3
    job_data = {
        "total": total,
        "completed": 0,
        "failed": 0,
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "completed_at": None
    }
    await save_job_status_to_s3(job_id, job_data, s3_client, S3_BUCKET, S3_PREFIX)

    print(f"[{job_id}] [BATCH START] → {total} URLs")

    # Process URLs sequentially (avoid overwhelming the system)
    for i, url in enumerate(urls, 1):
        try:
            await render_url_func(url)
            completed += 1
            print(f"[{job_id}] [{url}] [OK] → {completed}/{total}")
        except Exception as e:
            failed += 1
            print(f"[{job_id}] [{url}] [FAILED] → {e}")

        # Update progress every 10 URLs or at the end
        if i % 10 == 0 or i == total:
            job_data["completed"] = completed
            job_data["failed"] = failed
            await save_job_status_to_s3(job_id, job_data, s3_client, S3_BUCKET, S3_PREFIX)

    # Mark job as completed
    job_data["status"] = "completed"
    job_data["completed_at"] = datetime.now().isoformat()
    await save_job_status_to_s3(job_id, job_data, s3_client, S3_BUCKET, S3_PREFIX)

    print(f"[{job_id}] [BATCH COMPLETE] → {completed} success, {failed} failed")


def generate_job_id() -> str:
    # Generate unique job ID
    return str(uuid.uuid4())[:8]
