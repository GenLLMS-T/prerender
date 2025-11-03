#!/usr/bin/env node
var prerender = require('./lib');
var cachePlugin = require('./cache-plugin');

var server = prerender({
  chromeLocation: '/usr/bin/google-chrome',
  followRedirects: true,
});

// 캐시 플러그인 (가장 먼저!)
server.use(cachePlugin);

// server.use(prerender.sendPrerenderHeader());
server.use(prerender.browserForceRestart());
// server.use(prerender.blockResources());
server.use(prerender.addMetaTags());
server.use(prerender.removeScriptTags());
server.use(prerender.httpHeaders());

server.start();
