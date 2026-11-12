const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {stripTypeScriptTypes} = require('node:module');
const {webcrypto} = require('node:crypto');
const vm = require('node:vm');
const cms = 'https://cms.bahria.edu.pk';
const lms = 'https://lms.bahria.edu.pk';
const secret = 'a'.repeat(64);
const header = 'X-LMS-Proxy-Key';
const sources = {
  Netlify: 'proxies/netlify/netlify/edge-functions/proxy.ts',
  Vercel: 'proxies/vercel/api/proxy.ts',
  'Val Town': 'proxies/val-town/main.ts',
};
function handler(path, upstream, key = secret) {
  const source = stripTypeScriptTypes(readFileSync(path, 'utf8'))
    .replace('export default async function', 'globalThis.handle = async function')
    .replace('export const config', 'const config');
  const context = vm.createContext({Request, Response, Headers, URL, TextEncoder,
    crypto: webcrypto, Netlify: {env: {get: () => key}}, Deno: {env: {get: () => key}},
    process: {env: {LMS_PROXY_SERVICE_KEY: key}}, fetch: req => {
      assert.equal(req.redirect, 'manual');
      assert.equal(req.headers.get(header), null);
      assert.ok([cms, lms].includes(new URL(req.url).origin));
      return upstream(req);
    }});
  vm.runInContext(source, context);
  return context.handle;
}
function request(target, {native = true, key = secret, method = 'GET', body} = {}) {
  const url = new URL('https://proxy.example.test/');
  if (target !== null) url.searchParams.set('url', target);
  if (native) url.searchParams.set('native_redirects', 'true');
  const headers = {Cookie: 'session=synthetic'};
  if (key) headers[header] = key;
  return new Request(url, {headers, method, body});
}
for (const [name, path] of Object.entries(sources)) {
  test(name + ' fails closed for missing, wrong and absent configured keys', async () => {
    const fetch = () => assert.fail('unauthorized request reached upstream');
    const handle = handler(path, fetch);
    for (const key of ['', 'b'.repeat(64), 'short']) {
      for (const method of ['GET', 'POST', 'OPTIONS']) {
        assert.equal((await handle(request(lms, {key, method}))).status, 401);
      }
    }
    assert.equal((await handler(path, fetch, '')(request(lms))).status, 401);
  });
  test(name + ' rejects forbidden initial targets with zero fetches', async () => {
    const handle = handler(path, () => assert.fail('forbidden destination fetched'));
    for (const target of ['https://outside.example.test', 'http://lms.bahria.edu.pk',
      'https://lms.bahria.edu.pk:8443', 'https://user:pass@lms.bahria.edu.pk',
      'https://127.0.0.1', 'https://[::1]', 'https://lms.bahria.edu.pk.evil.test',
      'https://lms.bahria.edu.pk.', 'not a url', 'file:///etc/passwd']) {
      const response = await handle(request(target));
      assert.ok([400, 403].includes(response.status));
      assert.ok(!(await response.text()).includes(target));
    }
    assert.equal((await handle(request(null))).status, 400);
  });
  test(name + ' blocks off-list Location in native and transparent mode', async () => {
    for (const native of [true, false]) {
      let calls = 0;
      const handle = handler(path, () => { calls++; return new Response(null, {
        status: 302, headers: {Location: 'https://outside.example.test/stolen'}
      }); });
      const response = await handle(request(lms, {native}));
      assert.equal(response.status, 502);
      assert.equal(response.headers.get('Location'), null);
      assert.equal(calls, 1);
    }
  });
  test(name + ' keeps CMS fallback and LMS cookie/final URL response fields', async () => {
    const calls = [];
    const handle = handler(path, req => {
      calls.push(req.url);
      if (calls.length === 1) return new Response(null, {status: 302, headers: {
        Location: lms + '/home', 'Set-Cookie': 'moodle=synthetic; Secure'
      }});
      if (calls.length === 2) return new Response(null, {status: 302, headers: {Location: '/course'}});
      assert.match(req.headers.get('Cookie'), /moodle=synthetic/);
      return new Response('course data', {headers: {'X-Worker-Debug-Log': 'upstream-debug'}});
    });
    const response = await handle(request(cms + '/gateway'));
    assert.deepEqual(calls, [cms + '/gateway', lms + '/home', lms + '/course']);
    assert.equal(response.headers.get('X-Final-Url'), lms + '/course');
    assert.match(response.headers.get('X-Proxy-Set-Cookie'), /moodle=synthetic/);
    assert.equal(response.headers.get('X-Worker-Debug-Log'), null);
    assert.match(response.headers.get('Cache-Control'), /no-store/);
    assert.equal(await response.text(), 'course data');
  });
  test(name + ' preserves transparent downloads and canonical relative redirects', async () => {
    const bytes = new Uint8Array([0, 255, 20]);
    const handle = handler(path, () => new Response(bytes, {headers: {
      'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="fixture.bin"',
      'X-Worker-Debug-Log': 'upstream-debug'
    }}));
    const response = await handle(request(lms + '/file', {native: false}));
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
    assert.match(response.headers.get('Content-Disposition'), /fixture.bin/);
    assert.equal(response.headers.get('X-Worker-Debug-Log'), null);
    const redirect = handler(path, () => new Response(null, {status: 302, headers: {Location: '../next'}}));
    assert.equal((await redirect(request(lms + '/folder/file', {native:false}))).headers.get('Location'), lms + '/next');
  });
  test(name + ' streams POST then rewrites 302 to GET without leaking service key', async () => {
    const methods = [];
    const handle = handler(path, async req => {
      methods.push(req.method);
      if (methods.length === 1) {
        assert.equal(await req.text(), 'synthetic-body');
        return new Response(null, {status: 302, headers: {Location: '/complete'}});
      }
      assert.equal(await req.text(), '');
      return new Response('done');
    });
    assert.equal((await handle(request(lms, {method:'POST', body:'synthetic-body'}))).status, 200);
    assert.deepEqual(methods, ['POST', 'GET']);
  });
  test(name + ' rejects unsafe stream replay, preserves HEAD, bounds redirects', async () => {
    for (const status of [307, 308]) {
      let calls = 0;
      const handle = handler(path, () => { calls++; return new Response(null, {status, headers:{Location:'/next'}}); });
      assert.equal((await handle(request(lms, {method:'POST',body:'data'}))).status, 502);
      assert.equal(calls, 1);
    }
    let calls = 0;
    const handle = handler(path, req => {
      assert.equal(req.method, 'HEAD');
      calls++;
      return new Response(null, {status: 302, headers:{Location:'/next'}});
    });
    assert.equal((await handle(request(lms, {method:'HEAD'}))).status, 502);
    assert.equal(calls, 11);
  });
  test(name + ' returns generic errors and rejects a later malicious hop', async () => {
    const broken = handler(path, () => { throw new Error('sensitive session URL'); });
    assert.equal(await (await broken(request(lms))).text(), 'Proxy request failed.');
    let calls = 0;
    const handle = handler(path, () => new Response(null, {status:302,
      headers:{Location: ++calls === 1 ? '/allowed' : 'https://outside.example.test'}}));
    assert.equal((await handle(request(lms))).status, 502);
    assert.equal(calls, 2);
  });
}

test('Val Town does not forward compression headers for decoded Fetch bodies', async () => {
  for (const native of [false, true]) {
    const handle = handler(sources['Val Town'], req => {
      assert.equal(req.headers.get('Accept-Encoding'), 'identity');
      return new Response('<html>decoded fixture</html>', {headers: {
        'Content-Encoding': 'gzip', 'Content-Length': '19', 'Content-Type': 'text/html'
      }});
    });
    const response = await handle(request(lms + '/Student/Quizzes.php', {native}));
    assert.equal(response.headers.get('Content-Encoding'), null);
    assert.equal(response.headers.get('Content-Length'), null);
    assert.equal(await response.text(), '<html>decoded fixture</html>');
  }
});
