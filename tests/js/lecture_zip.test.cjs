const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');
const {deflateSync} = require('node:zlib');
const source = fs.readFileSync('cf_worker_merged.js', 'utf8').replace('export default {', 'globalThis.worker = {');
const keyHex = Buffer.alloc(32, 0x42).toString('hex');
const workerEnv = {DOWNLOAD_ENCRYPTION_KEY: keyHex};
async function token(files, age = 0) {
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const key = await webcrypto.subtle.importKey('raw', Buffer.from(keyHex, 'hex'), 'AES-GCM', false, ['encrypt']);
  const data = deflateSync(JSON.stringify({u: files, c: {session: 'secret'}, t: Date.now()/1000 - age}));
  return Buffer.concat([iv, Buffer.from(await webcrypto.subtle.encrypt({name:'AES-GCM', iv}, key, data))]).toString('base64url');
}
function runtime(fetch) {
  class FixedLengthStream extends TransformStream {
    constructor(total) { let count = 0; super({transform(chunk, controller) {
      count += chunk.length; assert(count <= total); controller.enqueue(chunk);
    }, flush() { assert.equal(count, total); }}); this.total = total; }
  }
  const context = vm.createContext({console: {error() {}, warn() {}}, fetch, Request, Response, Headers, Blob,
    TransformStream, FixedLengthStream, DecompressionStream, TextEncoder, TextDecoder,
    Uint8Array, Uint32Array, DataView, URL, URLSearchParams, AbortController,
    crypto: webcrypto, atob, setTimeout, clearTimeout});
  vm.runInContext(source, context);
  return context;
}
const url = 'https://lms.bahria.edu.pk/Student/file';
async function download(context, files, env = {}) {
  const form = new URLSearchParams({token: await token(files), filename: 'Lectures.zip'});
  return context.worker.fetch(new Request('https://worker.test/zip', {method:'POST', body:form}), {...workerEnv, ...env}, {});
}
function assertZip(bytes, expected) {
  let offset = 0;
  for (const [name, content] of expected) {
    assert.equal(bytes.readUInt32LE(offset), 0x04034b50);
    assert.equal(bytes.readUInt16LE(offset + 6), 0x0800, 'Android fix: no descriptors');
    assert.equal(bytes.readUInt16LE(offset + 8), 0, 'STORE');
    assert.equal(bytes.readUInt32LE(offset + 18), content.length);
    assert.equal(bytes.readUInt32LE(offset + 22), content.length);
    const size = bytes.readUInt16LE(offset + 26);
    assert.equal(bytes.subarray(offset + 30, offset + 30 + size).toString(), name);
    assert.deepEqual(bytes.subarray(offset + 30 + size, offset + 30 + size + content.length), content);
    offset += 30 + size + content.length;
  }
  assert.equal(bytes.readUInt32LE(offset), 0x02014b50);
  // Persist fixtures for independent Python extraction in the verification command.
  fs.mkdirSync('/tmp/unitrack-zip-tests', {recursive:true});
  fs.writeFileSync(`/tmp/unitrack-zip-tests/${expected[0][0].replace(/[^a-zA-Z0-9]/g, '_')}.zip`, bytes);

}
test('native POST produces exact Android-safe ZIP, Unicode, duplicates and empty files', async () => {
  const files = [{url, name:'نوٹس.pdf'}, {url:url+'2', name:'نوٹس.pdf'}, {url:url+'3', name:'empty.pdf'}];
  const context = runtime(async (target, options) => {
    const body = target.endsWith('3') ? Buffer.alloc(0) : Buffer.from('abc');
    return new Response(options.method === 'HEAD' ? null : body, {headers:{'Content-Length':body.length, 'Content-Type':'application/pdf'}});
  });
  const response = await download(context, files);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'private, no-store');
  const bytes = Buffer.from(await response.arrayBuffer());
  assertZip(bytes, [['نوٹس.pdf', Buffer.from('abc')], ['نوٹس_2.pdf', Buffer.from('abc')], ['empty.pdf', Buffer.alloc(0)]]);
});
test('missing metadata and extension still produces compatible archive', async () => {
  const context = runtime(async (_, options) => new Response(options.method === 'HEAD' ? null : '%PDF-data', {headers:{'Content-Type':'application/pdf'}}));
  const response = await download(context, [{url, name:'lecture'}]);
  assertZip(Buffer.from(await response.arrayBuffer()), [['lecture.pdf', Buffer.from('%PDF-data')]]);
});
test('size mismatch aborts rather than serving partial success', async () => {
  const context = runtime(async (_, options) => new Response(options.method === 'HEAD' ? null : 'abc', {headers:{'Content-Length':'4'}}));
  const response = await download(context, [{url, name:'a.pdf'}]);
  await assert.rejects(response.arrayBuffer(), /size changed/);
});
test('disallowed redirects never receive LMS cookies', async () => {
  let calls = 0;
  const context = runtime(async () => { calls++; return new Response(null, {status:302, headers:{Location:'https://evil.test/file'}}); });
  const response = await download(context, [{url, name:'a.pdf'}]);
  assert.equal(response.status, 502); assert.equal(calls, 1);
  assert.match(await response.text(), /lecture-zip-error/);
});
test('known oversized lecture returns individual-download guidance before transfer', async () => {
  const context = runtime(async () => new Response(null, {headers:{'Content-Length':17*1024*1024}}));
  const response = await download(context, [{url, name:'a.pdf'}]);
  assert.equal(response.status, 502); assert.match(await response.text(), /individually/);
});
test('unsupported HEAD skips range probes and streams without a total', async () => {
  const calls = [];
  const context = runtime(async (_, options) => {
    calls.push(options);
    if (options.method === 'HEAD') return new Response(null, {status:405});
    return new Response('abc');
  });
  const response = await download(context, [{url, name:'a.pdf'}]);
  assertZip(Buffer.from(await response.arrayBuffer()), [['a.pdf', Buffer.from('abc')]]);
  assert.equal(calls.length,2);
  assert(!calls.some(options => options.headers.Range));
});
test('expired token fails without contacting LMS', async () => {
  const context = runtime(() => {throw Error('must not fetch');});
  const response = await context.worker.fetch(new Request('https://worker.test/zip', {method:'POST', body:new URLSearchParams({token:await token([{url,name:'a.pdf'}],1000)})}), workerEnv, {});
  assert.equal(response.status, 401);
});
test('throttled file retries once; HTML login fails without ZIP completion', async () => {
  let gets = 0;
  const context = runtime(async (_, options) => {
    if (options.method === 'HEAD') return new Response(null, {headers:{'Content-Length':'3'}});
    if (++gets === 1) return new Response(null, {status:429, headers:{'Retry-After':'0.001'}});
    return new Response('abc');
  });
  assertZip(Buffer.from(await (await download(context, [{url,name:'a.pdf'}])).arrayBuffer()), [['a.pdf',Buffer.from('abc')]]);
  assert.equal(gets,2);
  const login = runtime(async (_, options) => new Response(options.method === 'HEAD' ? null : '<html>login', {headers:{'Content-Type':'text/html'}}));
  await assert.rejects((await download(login,[{url,name:'a.pdf'}])).arrayBuffer(), /login page/);
});
test('concurrency benchmark uses bounded parallelism with identical output', async () => {
  const files = Array.from({length:6}, (_, i) => ({url:url+i, name:`${i}.pdf`}));
  const runs = [];
  for (const concurrency of [1, 3]) {
    let active = 0, peak = 0;
    const context = runtime(async (_, options) => {
      if (options.method === 'HEAD') return new Response(null, {headers:{'Content-Length':'3'}});
      active++; peak = Math.max(peak, active);
      await new Promise(resolve => setTimeout(resolve, 20));
      active--;
      return new Response('abc');
    });
    const start = performance.now();
    const bytes = Buffer.from(await (await download(context, files, {ZIP_FETCH_CONCURRENCY:String(concurrency)})).arrayBuffer());
    runs.push({ms: Math.round(performance.now()-start), bytes, peak});
    assert(peak <= concurrency);
  }
  assert.deepEqual(runs[0].bytes, runs[1].bytes);
  assert.equal(runs[1].peak, 3);
  console.log(`Simulated latency benchmark: serial ${runs[0].ms} ms; parallel ${runs[1].ms} ms. Live LMS validation still required.`);
});
test('unknown oversized files abort and excessive file counts never fetch', async () => {
  let reads = 0;
  const context = runtime(async (_, options) => {
    if (options.method === 'HEAD') return new Response(null);
    if (options.headers.Range) return new Response('x');
    reads++;
    return new Response(new Uint8Array(16*1024*1024+1));
  });
  const response = await download(context,[{url,name:'a.pdf'}]);
  await assert.rejects(response.arrayBuffer(), /memory limit/);
  assert.equal(reads,1);
  const excessive = await download(context,Array.from({length:21},()=>({url,name:'a.pdf'})));
  assert.equal(excessive.status,502);
  assert.equal(reads,1);
});
test('cancelling output cancels upstream plan', async () => {
  let signal;
  const context = runtime(async (_, options) => {
    signal = options.signal;
    return new Response(options.method === 'HEAD' ? null : 'abc',{headers:{'Content-Length':'3'}});
  });
  const response = await download(context,[{url,name:'a.pdf'}]);
  await response.body.cancel();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(signal.aborted);
});
test('20-file exam part uses at most 40 upstream requests without range probes', async () => {
  let calls=0;
  const context=runtime(async (_,options)=>{
    calls++; assert(!options.headers.Range);
    return new Response(options.method==='HEAD'?null:'abc',{headers:{'Content-Length':'3'}});
  });
  const files=Array.from({length:20},(_,i)=>({url:url+i,name:`${i}.pdf`}));
  assertZip(Buffer.from(await (await download(context,files)).arrayBuffer()),files.map(file=>[file.name,Buffer.from('abc')]));
  assert.equal(calls,40);
});
test('metadata redirects cannot consume reserved download capacity', async () => {
  let calls=0;
  const context=runtime(async (target,options)=>{
    calls++;
    if(options.method==='HEAD') {
      const hop=Number(new URL(target).searchParams.get('hop')||0);
      if(hop<3) return new Response(null,{status:302,headers:{Location:url+'?hop='+(hop+1)}});
      return new Response(null,{headers:{'Content-Length':'3'}});
    }
    return new Response('abc');
  });
  const files=Array.from({length:20},(_,i)=>({url:url+i,name:`${i}.pdf`}));
  assertZip(Buffer.from(await (await download(context,files)).arrayBuffer()),files.map(file=>[file.name,Buffer.from('abc')]));
  assert(calls<=45);
});
test('native individual downloads stream files above the ZIP memory cap', async () => {
  const bytes=new Uint8Array(17*1024*1024);
  const context=runtime(async ()=>new Response(bytes,{headers:{'Content-Type':'application/pdf','Set-Cookie':'secret'}}));
  const form=new URLSearchParams({token:await token([{url,name:'big.pdf'}]),filename:'big.pdf'});
  const response=await context.worker.fetch(new Request('https://worker.test/download',{method:'POST',body:form}),workerEnv,{});
  assert.equal(response.status,200);
  assert.equal(response.headers.get('Set-Cookie'),null);
  assert.match(response.headers.get('Content-Disposition'),/attachment/);
  assert.equal((await response.arrayBuffer()).byteLength,bytes.length);
});
test('POST body cap applies without a Content-Length header', async () => {
  const context=runtime(()=>{throw new Error('Must not contact LMS');});
  const request=new Request('https://worker.test/zip',{method:'POST',body:'token='+'x'.repeat(262144)});
  assert.equal(request.headers.get('Content-Length'),null);
  const response=await context.worker.fetch(request,workerEnv,{});
  assert.equal(response.status,413);
});
