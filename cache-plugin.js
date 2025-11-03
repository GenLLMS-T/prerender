const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const CACHE_DIR = path.join(__dirname, 'cache');

// 캐시 디렉토리 생성
if (!fs.existsSync(CACHE_DIR)) {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
}

// URL을 파일명으로 변환 (해시 사용)
function urlToFilename(url) {
  return crypto.createHash('md5').update(url).digest('hex') + '.html';
}

// URL 매핑 파일 경로
const MAP_FILE = path.join(CACHE_DIR, '_url_map.json');

// URL 매핑 로드
function loadUrlMap() {
  if (fs.existsSync(MAP_FILE)) {
    return JSON.parse(fs.readFileSync(MAP_FILE, 'utf8'));
  }
  return {};
}

// URL 매핑 저장
function saveUrlMap(map) {
  fs.writeFileSync(MAP_FILE, JSON.stringify(map, null, 2));
}

module.exports = {
  requestReceived: function(req, res, next) {
    const url = req.prerender.url;
    const urlMap = loadUrlMap();
    const filename = urlMap[url];

    if (filename) {
      const filePath = path.join(CACHE_DIR, filename);

      if (fs.existsSync(filePath)) {
        const html = fs.readFileSync(filePath, 'utf8');
        console.log(`[CACHE HIT] ${url}`);

        // prerender가 인식하도록 설정
        req.prerender.content = html;
        req.prerender.statusCode = 200;
        req.prerender.responseSent = true;

        res.send(html);
        return; // 캐시 반환, next() 호출 안함
      }
    }

    console.log(`[CACHE MISS] ${url}`);
    next(); // 캐시 없으면 렌더링 진행
  },

  beforeSend: function(req, res, next) {
    const url = req.prerender.url;
    const html = req.prerender.content;

    if (html) {
      const filename = urlToFilename(url);
      const filePath = path.join(CACHE_DIR, filename);

      // HTML 저장
      fs.writeFileSync(filePath, html);

      // URL 매핑 업데이트
      const urlMap = loadUrlMap();
      urlMap[url] = filename;
      saveUrlMap(urlMap);

      console.log(`[CACHE SAVED] ${url} -> ${filename}`);
    }

    next();
  }
};