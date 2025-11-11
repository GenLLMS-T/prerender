# Prerender Service

Playwright 기반의 경량화된 프리렌더링 서비스입니다. JavaScript로 렌더링된 페이지를 캐싱하여 SEO와 성능을 개선합니다.

## 주요 특징

- **Playwright 기반**: Chromium을 사용한 안정적인 렌더링
- **비동기 워커 풀**: 10개의 워커가 동시에 렌더링 처리
- **3단계 캐시 시스템**: Redis (인메모리, ~1ms) → S3 (영구 저장, ~50ms) → 렌더링
- **중복 요청 처리**: 동일 URL 동시 요청 시 첫 번째 결과 공유
- **SSRF 방어**: 사설 IP 및 로컬호스트 차단
- **일자별 로깅**: 성공/부분/실패 상태별 로깅 및 중복 제거

## 기존 prerender.io와의 차이점

| 항목 | prerender.io (Node.js) | 현재 (Playwright) |
|------|----------------------|------------------|
| 브라우저 | Chrome CDP | Playwright Chromium |
| 캐시 | 로컬 파일 | AWS S3 |
| 언어 | Node.js | Python (FastAPI) |
| 워커 | 탭별 처리 | 워커 풀 10개 |
| 플러그인 | 다양한 플러그인 | 미니멀 (필요 시 추가) |

## 프로젝트 구조

```
.
├── main.py           # FastAPI 앱 및 /render 엔드포인트
├── worker.py         # Playwright 렌더링 워커
├── config.py         # 환경변수 설정 중앙 관리
├── requirements.txt  # Python 의존성
├── Dockerfile        # Docker 이미지 빌드
├── docker-compose.yml
└── .env             # 환경변수 설정
```

## 환경 설정

`.env` 파일을 생성하고 다음 내용을 설정하세요:

```env
# Runtime mode
MODE=production

# S3 Configuration
SITEMAPLLMS_S3_REGION=ap-northeast-2
SITEMAPLLMS_S3_BUCKET=your-bucket-name
SITEMAPLLMS_S3_PREFIX=prerender
SITEMAPLLMS_S3_ACCESS_KEY=your-access-key
SITEMAPLLMS_S3_SECRET_KEY=your-secret-key
SITEMAPLLMS_S3_USE_SSL=true

# Prerender Configuration
NUM_WORKERS=10
PAGE_LOAD_TIMEOUT=5000
META_LOADER_TIMEOUT=2000
PRERENDER_PORT=3081

# Redis Configuration (docker-compose에서 자동 설정)
REDIS_URL=redis://redis:6379
REDIS_CACHE_TTL=3600      # 완전 렌더링 캐시 TTL (1시간)
REDIS_RENDER_TTL=60       # 중복 요청용 TTL (60초)
REDIS_FAILURE_TTL=300     # 실패 캐싱 TTL (5분)
```

## 로컬 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 2. 서버 실행

```bash
uvicorn main:app --host 0.0.0.0 --port 3081
```

## Docker 실행

```bash
docker-compose up --build
```

## API 사용법

### 렌더링 요청

```bash
GET http://localhost:3081/render?url=https://example.com
```

**응답:**
```
Status: 200
Content-Type: text/html

<html>...</html>
```

또는 렌더링 실패 시 원본 URL로 302 리다이렉트

### 배치 렌더링

**1. Sitemap에서 배치 렌더링:**
```bash
curl -X POST http://localhost:3081/batch/sitemap \
  -H "Content-Type: application/json" \
  -d '{"sitemap_url": "https://example.com/sitemap.xml"}'
```

**응답:**
```json
{
  "status": "started",
  "job_id": "a1b2c3d4",
  "total_urls": 1523,
  "message": "Batch job started. Check progress at GET /batch/status/a1b2c3d4"
}
```

**2. 파일 업로드로 배치 렌더링:**
```bash
# failed_urls.txt 파일 업로드
curl -X POST http://localhost:3081/batch/file \
  -F "file=@logs/failed_urls.txt"

# 또는 다른 URL 목록 파일
curl -X POST http://localhost:3081/batch/file \
  -F "file=@urls.txt"
```

파일 형식 (newline-separated):
```
https://example.com/page1
https://example.com/page2
https://example.com/page3
```

**3. 진행 상황 확인:**
```bash
curl http://localhost:3081/batch/status/a1b2c3d4
```

**응답:**
```json
{
  "job_id": "a1b2c3d4",
  "status": "running",
  "total": 1523,
  "completed": 450,
  "failed": 12,
  "progress": "450/1523",
  "started_at": "2025-11-10T12:30:00",
  "completed_at": null
}
```

**상태 값:**
- `running`: 진행 중
- `completed`: 완료

**저장 위치:**
- 배치 작업 상태: S3 (`{S3_PREFIX}/batch/{job_id}.json`)
- 영구 저장되며, 수동 삭제 필요

### 동작 흐름 (3단계 캐시)

1. 클라이언트가 `/render?url=...` 요청
2. **Redis 캐시 확인** (완전 렌더링만, TTL: 1시간)
   - ✅ 히트: HTML 즉시 반환 (~1ms)
   - ❌ 미스: 다음 단계
3. **S3 캐시 확인** (MD5 해시 기반)
   - ✅ 히트: HTML 반환 + Redis에 캐시 (~50ms)
   - ❌ 미스: 렌더링 시작
4. **렌더링** (최대 7초: 페이지 로드 5초 + 메타 태그 대기 2초)
   - **중복 요청 처리**: Lock 확인
     - 다른 요청이 렌더링 중이면 대기 (최대 60초)
     - 결과 공유하여 중복 렌더링 방지
   - 세마포어 획득 (최대 10개 동시 처리)
   - Playwright로 페이지 렌더링
   - 완전한 렌더링 시:
     - S3에 영구 저장
     - Redis에 캐시 (TTL: 1시간)
   - 실패 시:
     - Redis에 실패 기록 (TTL: 5분, 재시도 방지)
     - 원본 URL로 302 리다이렉트

## 렌더링 완료 조건

페이지가 다음 메타 태그를 생성할 때까지 대기합니다:

```html
<meta data-gen-source="meta-loader">
```

이 태그가 나타나면 렌더링 완료로 간주합니다. 타임아웃은 `META_LOADER_TIMEOUT` 환경변수로 설정 가능합니다.

## 로깅 시스템

모든 렌더링 요청이 상태별로 로깅됩니다:

**렌더링 상태**:
- **SUCCESS**: 완전한 렌더링 + S3 캐싱 완료
- **PARTIAL**: 페이지 로드 성공, 메타 태그 타임아웃 (부분 렌더링)
- **FAILED**: 페이지 로드 실패 또는 예외 발생

**로그 파일**:
- `logs/render-YYYY-MM-DD.log`: 일자별 전체 렌더링 로그 (성공/부분/실패 모두 포함)
- `logs/failed_urls.txt`: 실패한 URL 목록 (중복 제거됨, batch 재처리용)

로그 파일은 호스트의 `./logs` 디렉토리에 저장되며, 컨테이너를 재시작해도 유지됩니다.

**로그 확인 방법**:
```bash
# 오늘자 렌더링 로그
tail -f logs/render-$(date +%Y-%m-%d).log

# 실패한 URL 목록 (중복 제거됨)
cat logs/failed_urls.txt

# Docker logs에서 실시간 확인
docker logs -f prerender
```

**콘솔 출력 예시**:
```
[2025-11-10 14:30:15] [SUCCESS] https://example.com
  → rendered and cached

[2025-11-10 14:30:20] [PARTIAL] https://slow.com
  → meta tag timeout (partial render)

[2025-11-10 14:30:25] [FAILED] https://broken.com
  → page load timeout
```

## FastAPI 메인 프로젝트와 통합

현재는 독립 컨테이너로 실행되지만, 필요 시 FastAPI 메인 프로젝트에 통합 가능합니다:

```yaml
# docker-compose.yml에 추가
services:
  prerender:
    build: ./prerender
    expose:
      - "3081"
    env_file:
      - ./prerender/.env
```

메인 FastAPI에서 호출:

```python
import httpx

async def get_prerendered_page(url: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://prerender:3081/render",
            params={"url": url}
        )
        return response.text
```

## 설정 튜닝

**렌더링 설정:**
- `NUM_WORKERS`: 동시 렌더링 워커 수 (기본: 10)
- `PAGE_LOAD_TIMEOUT`: 페이지 로드 타임아웃 (기본: 5000ms, DOM 파싱 완료까지)
- `META_LOADER_TIMEOUT`: 메타태그 대기 타임아웃 (기본: 2000ms, JavaScript 렌더링 완료 대기)

**Redis 캐시 TTL:**
- `REDIS_CACHE_TTL`: 완전 렌더링 캐시 (기본: 3600초 = 1시간)
  - Redis에 HTML을 캐싱하는 시간
  - 자주 방문하는 페이지는 ~1ms 응답 속도
- `REDIS_RENDER_TTL`: 중복 요청용 임시 캐시 (기본: 60초)
  - 동일 URL 동시 요청 시 결과 공유
  - 렌더링 완료 후 1분간 유지
- `REDIS_FAILURE_TTL`: 실패 캐싱 (기본: 300초 = 5분)
  - 렌더링 실패한 URL을 5분간 기록
  - 재시도 폭주(retry storm) 방지

## 주의사항

- S3 버킷은 미리 생성되어 있어야 합니다

## 라이선스

Apache License 2.0