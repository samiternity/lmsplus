const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {stripTypeScriptTypes} = require('node:module');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const {deflateSync} = require('node:zlib');

const cms = 'https://cms.bahria.edu.pk';
const lms = 'https://lms.bahria.edu.pk';
const blocked = 'https://outside.example.test';
const key = Buffer.alloc(32, 0x42);
const serviceKey = Buffer.alloc(32, 0x61).toString('hex');
const serviceHeader = 'X-LMS-Proxy-Key';
const workerSource = readFileSync('cf_worker_merged.js', 'utf8')
  .replace('export default {', 'globalThis.worker = {');
const supabaseSource = stripTypeScriptTypes(
  readFileSync('supabase/functions/proxy/index.ts', 'utf8')
    .replace(/^import \{ serve \}[^\n]*\n/, '')
);

function proxy(source, fetch, env = {LMS_PROXY_SERVICE_KEY: serviceKey, DOWNLOAD_ENCRYPTION_KEY: key.toString('hex')}) {
  let handle;
  const context = vm.createContext({
    Request, Response, Headers, URL, URLSearchParams, TextEncoder, TextDecoder,
    Blob, DecompressionStream, crypto: webcrypto, atob, setTimeout, clearTimeout,
    serve: fn => { handle = fn; },
    Deno: {env: {get: key => env[key]}},
    fetch: (input, init) => {
      assert.equal(init.redirect, 'manual');
      const request = typeof input === 'string' ? {url: input, ...init} : input;
      assert.equal(new Headers(request.headers).get(serviceHeader), null);
      return fetch(request);
    }
  });
  vm.runInContext(source, context);
  return handle || (request => context.worker.fetch(request, env));
}

function transparent(handle, target, follow = false) {
  const url = new URL('https://proxy.example.test/');
  url.searchParams.set('url', target);
  if (follow) url.searchParams.set('native_redirects', 'true');
  return handle(new Request(url, {headers: {Cookie: 'session=synthetic', [serviceHeader]: serviceKey}}));
}

function macro(handle, action) {
  return handle(new Request('https://proxy.example.test/?macro=true', {
    method: 'POST',
    headers: {[serviceHeader]: serviceKey},
    body: JSON.stringify({action, username: 'synthetic-user', password: 'synthetic-password'})
  }));
}

const viewstate = ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION']
  .map(field => '<input name="' + field + '" value="fixture">').join('');
const login = cms + '/Logins/Student/Login.aspx';

for (const [name, source] of [['Worker', workerSource], ['Supabase', supabaseSource]]) {
  test(name + ' blocks missing and wrong service credentials before contacting the LMS', async () => {
    const handle = proxy(source, () => assert.fail('unauthorized request reached upstream'));
    const target = 'https://proxy.example.test/?url=' + encodeURIComponent(lms + '/file');
    for (const headers of [{}, {[serviceHeader]: Buffer.alloc(32, 0x62).toString('hex')}]) {
      assert.equal((await handle(new Request(target, {headers}))).status, 401);
      assert.equal((await handle(new Request('https://proxy.example.test/?macro=true', {
        method: 'POST', headers, body: '{}'
      }))).status, 401);
    }
    const unconfigured = proxy(source, () => assert.fail('unconfigured proxy reached upstream'), {});
    assert.equal((await transparent(unconfigured, lms + '/file')).status, 401);
  });

  test(name + ' rejects malformed and off-list initial targets before fetch', async () => {
    const requests = [];
    const handle = proxy(source, request => { requests.push(request); return new Response('unexpected'); });
    for (const target of [
      blocked, 'http://lms.bahria.edu.pk/file', 'https://extra.bahria.edu.pk/file',
      'https://lms.bahria.edu.pk.example.test/file', 'https://lms.bahria.edu.pk:8443/file',
      'https://user:password@lms.bahria.edu.pk/file', 'https://127.0.0.1/file',
      'https://[::1]/file', 'blob:https://lms.bahria.edu.pk/file'
    ]) {
      assert.equal((await transparent(handle, target)).status, 403);
    }
    assert.equal((await transparent(handle, 'relative/file')).status, 400);
    assert.deepEqual(requests, []);
  });

  test(name + ' follows approved relative and CMS-to-LMS redirects', async () => {
    const requests = [];
    const handle = proxy(source, request => {
      requests.push(request.url);
      if (request.url === cms + '/start') {
        return new Response(null, {status: 302, headers: {Location: '/gateway', 'Set-Cookie': 'cms=synthetic'}});
      }
      assert.match(request.headers.get('Cookie'), /cms=synthetic/);
      if (request.url === cms + '/gateway') {
        return new Response(null, {status: 302, headers: {Location: lms + '/course'}});
      }
      return new Response('course', {headers: {'X-Worker-Debug-Log': 'private fixture'}});
    });
    const response = await transparent(handle, cms + '/start', true);
    assert.equal(await response.text(), 'course');
    assert.equal(response.headers.get('X-Final-Url'), lms + '/course');
    assert.equal(response.headers.get('X-Proxy-Set-Cookie'), 'cms=synthetic');
    assert.equal(response.headers.get('X-Worker-Debug-Log'), null);
    assert.deepEqual(requests, [cms + '/start', cms + '/gateway', lms + '/course']);
  });

  test(name + ' returns a canonical approved Location in pass-through mode', async () => {
    const handle = proxy(source, () => new Response(null, {
      status: 302, headers: {Location: '/gateway', 'X-Worker-Debug-Log': 'private fixture'}
    }));
    const response = await transparent(handle, cms + '/start');
    assert.equal(response.status, 302);
    assert.equal(response.headers.get('Location'), cms + '/gateway');
    assert.equal(response.headers.get('X-Worker-Debug-Log'), null);
  });

  test(name + ' rejects off-list Location in either redirect mode', async () => {
    for (const follow of [false, true]) {
      const requests = [];
      const handle = proxy(source, request => {
        requests.push(request.url);
        return new Response(null, {status: 302, headers: {Location: '//outside.example.test/secret'}});
      });
      const response = await transparent(handle, cms + '/start', follow);
      assert.equal(response.status, 502);
      assert.equal(response.headers.get('Location'), null);
      assert.equal(await response.text(), 'Upstream destination is not allowed.');
      assert.deepEqual(requests, [cms + '/start']);
    }
  });

  test(name + ' validates redirects while obtaining the CMS login page', async () => {
    const requests = [];
    const handle = proxy(source, request => {
      requests.push(request.url);
      return new Response(null, {status: 302, headers: {Location: blocked + '/login'}});
    });
    const response = await macro(handle, 'lms_gateway');
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error, 'Upstream destination is not allowed.');
    assert.deepEqual(requests, [login]);
  });

  test(name + ' rejects a survey form action before posting credentials or session data', async () => {
    const requests = [];
    const handle = proxy(source, request => {
      requests.push([request.url, request.method || 'GET']);
      if (request.url === login) {
        return request.method === 'POST'
          ? new Response(null, {status: 302, headers: {Location: '/signed-in', 'Set-Cookie': 'cms=synthetic'}})
          : new Response(viewstate);
      }
      if (request.url.endsWith('QualityAssuranceSurveys.aspx')) {
        return new Response('<tr><td><a href="SurveyStudentCourseWise.aspx">Survey</a></td></tr>');
      }
      if (request.url.endsWith('SurveyStudentCourseWise.aspx')) {
        return new Response('<form action="' + blocked + '/submit">' + viewstate + '</form>');
      }
      assert.equal(request.url, cms + '/signed-in');
      return new Response('signed in');
    });
    const response = await macro(handle, 'autofill_surveys');
    assert.equal(response.status, 403);
    assert.equal((await response.json()).error, 'Upstream destination is not allowed.');
    assert.equal(requests.length, 5);
    assert.deepEqual(requests.filter(([, method]) => method === 'POST'), [[login, 'POST']]);
  });

  test(name + ' resolves a relative form action against the final survey page', async () => {
    const posts = [];
    const handle = proxy(source, request => {
      if (request.method === 'POST') posts.push(request.url);
      if (request.url === login) {
        return request.method === 'POST'
          ? new Response(null, {status: 302, headers: {Location: '/signed-in'}})
          : new Response(viewstate);
      }
      if (request.url.endsWith('QualityAssuranceSurveys.aspx')) {
        return new Response('<tr><td><a href="SurveyStudentCourseWise.aspx">Survey</a></td></tr>');
      }
      if (request.url.endsWith('SurveyStudentCourseWise.aspx')) {
        return new Response(null, {status: 302, headers: {Location: '/redirected/form'}});
      }
      if (request.url === cms + '/redirected/form') {
        return new Response('<form action="submit">' + viewstate + '</form>');
      }
      if (request.url.endsWith('/GoToLMS.aspx')) {
        return new Response(null, {status: 302, headers: {Location: lms + '/course'}});
      }
      assert([cms + '/signed-in', cms + '/redirected/submit', lms + '/course'].includes(request.url));
      return new Response('fixture');
    });
    const response = await macro(handle, 'autofill_surveys');
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('X-Final-Url'), lms + '/course');
    assert.deepEqual(posts, [login, cms + '/redirected/submit']);
  });
}

async function fileRequest(handle, route, target, init) {
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const encryptionKey = await webcrypto.subtle.importKey('raw', key, 'AES-GCM', false, ['encrypt']);
  const payload = deflateSync(JSON.stringify({u: [target], c: {session: 'synthetic'}, t: Date.now() / 1000}));
  const ciphertext = await webcrypto.subtle.encrypt({name: 'AES-GCM', iv}, encryptionKey, payload);
  const token = Buffer.concat([iv, Buffer.from(ciphertext)]).toString('base64url');
  return handle(new Request('https://proxy.example.test/' + route + '?token=' + token, init));
}

test('Worker legacy download and streaming upload accept LMS targets only', async () => {
  const requests = [];
  const handle = proxy(workerSource, async request => {
    requests.push(request.url);
    assert.match(new Headers(request.headers).get('Cookie'), /session=synthetic/);
    if (request.method === 'POST') {
      assert.equal(await new Response(request.body).text(), 'synthetic upload bytes');
      return new Response(null, {status: 302, headers: {Location: '/success'}});
    }
    return new Response('synthetic download', {headers: {'X-Worker-Debug-Log': 'private fixture'}});
  });
  const upload = {method: 'POST', body: 'synthetic upload bytes'};
  assert.equal((await fileRequest(handle, 'download', cms + '/file')).status, 403);
  assert.equal((await fileRequest(handle, 'upload', blocked + '/submit', upload)).status, 403);
  assert.deepEqual(requests, []);

  const download = await fileRequest(handle, 'download', lms + '/file');
  assert.equal(await download.text(), 'synthetic download');
  assert.equal(download.headers.get('X-Worker-Debug-Log'), null);
  const submitted = await fileRequest(handle, 'upload', lms + '/submit', upload);
  assert.equal((await submitted.json()).success, true);
  assert.deepEqual(requests, [lms + '/file', lms + '/submit']);
});

test('Worker upload rejects off-list Location and legacy download still rejects redirects', async () => {
  const requests = [];
  const handle = proxy(workerSource, request => {
    requests.push(request.url);
    return new Response(null, {status: 302, headers: {Location: blocked + '/secret'}});
  });
  const upload = await fileRequest(handle, 'upload', lms + '/submit', {method: 'POST', body: 'fixture'});
  assert.equal(upload.status, 502);
  assert.equal(upload.headers.get('Location'), null);
  assert.equal((await fileRequest(handle, 'download', lms + '/file')).status, 401);
  assert.deepEqual(requests, [lms + '/submit', lms + '/file']);
});

test('Worker rejects file tokens when its file key binding is missing', async () => {
  const handle = proxy(workerSource, () => assert.fail('file request reached upstream'), {
    LMS_PROXY_SERVICE_KEY: serviceKey
  });
  assert.equal((await fileRequest(handle, 'download', lms + '/file')).status, 401);
});
