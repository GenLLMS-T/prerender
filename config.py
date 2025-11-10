import os
from dotenv import load_dotenv

load_dotenv()

# S3 Configuration
S3_REGION = os.getenv("SITEMAPLLMS_S3_REGION", "ap-northeast-2")
S3_BUCKET = os.getenv("SITEMAPLLMS_S3_BUCKET", "genllms")
S3_PREFIX = os.getenv("SITEMAPLLMS_S3_PREFIX", "prerender")
S3_ACCESS_KEY = os.getenv("SITEMAPLLMS_S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("SITEMAPLLMS_S3_SECRET_KEY")
S3_USE_SSL = os.getenv("SITEMAPLLMS_S3_USE_SSL", "true").lower() == "true"

# Prerender Configuration
NUM_WORKERS = int(os.getenv("NUM_WORKERS", "10"))
PAGE_LOAD_TIMEOUT = int(os.getenv("PAGE_LOAD_TIMEOUT", "5000"))
META_LOADER_TIMEOUT = int(os.getenv("META_LOADER_TIMEOUT", "2000"))
PRERENDER_PORT = int(os.getenv("PRERENDER_PORT", "3081"))
