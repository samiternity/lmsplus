const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync('cf_worker_merged.js', 'utf8').replace('export default {', 'globalThis.worker = {');
const keyHex = '22'.repeat(32);
const env = {LMS_MCP_DOWNLOAD_KEY: keyHex};

async function token(changes = {}, alternateKey) {
  const issued = Math.floor(Date.now()/1000);
  const payload = {purpose: 'mcp-assignment-download', url: 'https://lms.bahria.edu.pk/Student/assignment.pdf',
    cookies: {PHPSESSID: 'private-cookie'}, issued, expires: issued+120, ...changes};
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const key = await webcrypto.subtle.importKey('raw', Buffer.from(alternateKey || keyHex, 'hex'), 'AES-GCM', false, ['encrypt']);
  const cipher = await webcrypto.subtle.encrypt({name:'AES-GCM', iv, additionalData:new TextEncoder().encode('mcp-download-v1')},
                                               key, new TextEncoder().encode(JSON.stringify(payload)));
  return Buffer.concat([iv, Buffer.from(cipher)]).toString('base64url');
}
function runtime(fetch) {
  const context = vm.createContext({console, fetch, Request, Response, Headers, Blob, TransformStream, ReadableStream,
    DecompressionStream, TextEncoder, TextDecoder, Uint8Array, Uint32Array, DataView, URL,
    URLSearchParams, AbortController, crypto:webcrypto, atob, setTimeout, clearTimeout});
  vm.runInContext(source, context);
  return context.worker;
}
async function request(worker, authorization, settings=env) {
  return worker.fetch(new Request('https://edge.test/mcp-download', {method:'POST', body:new URLSearchParams({token:authorization})}), settings, {});
}
test('connector downloads fail closed without a dedicated key', async () => {
  const worker = runtime(() => {throw Error('must not fetch');});
  assert.equal((await request(worker, await token(), {})).status,404);
  assert.equal((await worker.fetch(new Request('https://edge.test/mcp-download'),env,{})).status,405);
});
test('purpose, lifetime, key, destinations, and cookie headers are validated before fetch', async () => {
  const worker = runtime(() => {throw Error('must not fetch');});
  for (const change of [{purpose:'download'}, {issued:1,expires:121}, {expires:Math.floor(Date.now()/1000)+900},
    {url:'https://evil.test/file.pdf'}, {url:'https://lms.bahria.edu.pk.evil.test/file.pdf'},
    {url:'http://lms.bahria.edu.pk/file.pdf'}, {url:'https://user:password@lms.bahria.edu.pk/file.pdf'},
    {cookies:{session:'value\r\nInjected: true'}}]) {
    assert.equal((await request(worker,await token(change))).status,401);
  }
  assert.equal((await request(worker,await token({},'33'.repeat(32)))).status,401);
});
test('valid request streams bytes and excludes upstream cookies and debug headers', async () => {
  const worker = runtime(async (url, options) => {
    assert.equal(url,'https://lms.bahria.edu.pk/Student/assignment.pdf');
    assert.equal(options.redirect,'manual');
    assert(options.headers.Cookie.includes('PHPSESSID=private-cookie'));
    return new Response('%PDF-test-content', {headers:{'Content-Type':'application/pdf','Set-Cookie':'private','X-Worker-Debug-Log':'private'}});
  });
  const response = await request(worker,await token());
  assert.equal(response.status,200);
  assert.equal(await response.text(),'%PDF-test-content');
  assert.equal(response.headers.get('Cache-Control'),'private, no-store');
  assert.equal(response.headers.get('Set-Cookie'),null);
  assert.equal(response.headers.get('X-Worker-Debug-Log'),null);
});
test('redirects, disguised login pages, missing files, and oversized responses are rejected', async () => {
  const cases = [
    [new Response(null,{status:302,headers:{Location:'https://evil.test'}}),401],
    [new Response('<html>login',{headers:{'Content-Type':'text/html'}}),401],
    [new Response('<html>login',{headers:{'Content-Type':'application/pdf'}}),401],
    [new Response('missing',{status:404}),404],
    [new Response('%PDF-test',{headers:{'Content-Length':String(11*1024*1024)}}),413]
  ];
  for (const [upstream,status] of cases) {
    const response = await request(runtime(async () => upstream),await token());
    assert.equal(response.status,status);
    assert(!((await response.text()).includes('private-cookie')));
  }
});
