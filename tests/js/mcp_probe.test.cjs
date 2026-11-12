const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');
const {deflateSync} = require('node:zlib');
const source = fs.readFileSync('mcp_probe_worker.js', 'utf8').replace('export default {', 'globalThis.worker = {');
const keyHex = '11'.repeat(32);
const env = {MCP_PROBE_ENCRYPTION_KEY:keyHex, MCP_PROBE_ORIGIN:'https://unitrack-testing.web.app'};

async function token(changes = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {p:'mcp-probe', case:'pdf', t:now, e:now+120, ...changes};
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const key = await webcrypto.subtle.importKey('raw', Buffer.from(keyHex, 'hex'), 'AES-GCM', false, ['encrypt']);
  const cipher = await webcrypto.subtle.encrypt({name:'AES-GCM',iv}, key, deflateSync(JSON.stringify(payload)));
  return Buffer.concat([iv, Buffer.from(cipher)]).toString('base64url');
}
function runtime(fetch) {
  const context = vm.createContext({console, fetch, Request, Response, Headers, Blob, TransformStream,
    DecompressionStream, TextEncoder, TextDecoder, Uint8Array, Uint32Array, DataView, URL,
    URLSearchParams, AbortController, crypto:webcrypto, atob, setTimeout, clearTimeout});
  vm.runInContext(source, context);
  return context.worker;
}
async function request(worker, value, config = env, method = 'GET') {
  return worker.fetch(new Request(`https://edge.test/mcp-probe-download?token=${value}`, {method}), config, {});
}
test('probe is disabled without explicit testing origin and real encryption key', async () => {
  const worker = runtime(() => {throw Error('must not fetch');});
  assert.equal((await request(worker, await token(), {})).status, 404);
  assert.equal((await request(worker, await token(), {...env, MCP_PROBE_ORIGIN:'https://evil.test'})).status,404);
  assert.equal((await request(worker, await token(), {...env, MCP_PROBE_ENCRYPTION_KEY:''})).status,404);
});
test('expired, wrong-purpose, unknown-format and long-lived links never fetch', async () => {
  const worker = runtime(() => {throw Error('must not fetch');});
  for (const change of [{p:'download'}, {case:'pptx'}, {t:1000,e:1120}, {e:Math.floor(Date.now()/1000)+900}]) {
    assert.equal((await request(worker, await token(change))).status,401);
  }
  assert.equal((await request(worker,'bad')).status,401);
  assert.equal((await request(worker,await token(),env,'POST')).status,405);
});
test('valid link streams exact bytes from fixed testing origin without cookies', async () => {
  let calls = 0;
  const worker = runtime(async (url, options) => {
    calls++;
    assert(url.startsWith('https://unitrack-testing.web.app/mcp-probe/files/pdf?token='));
    assert.equal(options.redirect,'manual');
    assert.equal(options.cache,'no-store');
    assert(!options.headers.Cookie);
    return new Response('%PDF-test', {headers:{'Content-Type':'application/pdf', 'Set-Cookie':'secret'}});
  });
  const response = await request(worker,await token());
  assert.equal(response.status,200);
  assert.equal(await response.text(),'%PDF-test');
  assert.equal(response.headers.get('Cache-Control'),'private, no-store');
  assert.equal(response.headers.get('Set-Cookie'),null);
  assert.equal(calls,1);
});
test('redirects, HTML logins, absent bodies and upstream failures are rejected', async () => {
  for (const response of [new Response(null,{status:302,headers:{Location:'https://evil.test'}}),
    new Response('<html>login',{headers:{'Content-Type':'text/html'}}),
    new Response(null,{headers:{'Content-Type':'application/pdf'}}),
    new Response('expired',{status:401})]) {
    const worker = runtime(async () => response);
    const result = await request(worker,await token());
    assert([401,502].includes(result.status));
    assert(!((await result.text()).includes('evil.test')));
  }
});
