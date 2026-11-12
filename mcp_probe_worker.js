/** Testing-only synthetic document relay. No university credentials or assignment bytes are stored. */
function hexToBytes(hex) {
  let bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i !== bytes.length; i++) {
    bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return bytes;
}

function base64UrlToBytes(base64) {
  const b64 = base64.replace(/-/g, '+').replace(/_/g, '/');
  const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
  const binary_string = atob(b64 + pad);
  const len = binary_string.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary_string.charCodeAt(i);
  }
  return bytes;
}

async function decryptToken(token, env) {
  const keyHex = env.MCP_PROBE_ENCRYPTION_KEY;
  const keyBytes = hexToBytes(keyHex);
  const tokenBytes = base64UrlToBytes(token);
  const iv = tokenBytes.slice(0, 12);
  const ciphertextAndTag = tokenBytes.slice(12);

  const cryptoKey = await crypto.subtle.importKey(
    "raw", keyBytes, { name: "AES-GCM" }, false, ["decrypt"]
  );

  const decryptedCompressedBytes = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: iv }, cryptoKey, ciphertextAndTag
  );

  const ds = new DecompressionStream('deflate');
  const response = new Response(
      new Blob([decryptedCompressedBytes]).stream().pipeThrough(ds)
  );
  const jsonString = await response.text();
  return JSON.parse(jsonString);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // Private ingestion experiment: fixed synthetic fixtures, never student files.
    // Enable only on a testing Worker; no arbitrary destination URL is accepted.
    if (url.pathname === '/mcp-probe-download') {
      const failure = (status, error) => new Response(JSON.stringify({error}), {
        status, headers: {'Content-Type': 'application/json', 'Cache-Control': 'private, no-store'}
      });
      if (request.method !== 'GET') return failure(405, 'GET required');
      if (env.MCP_PROBE_ORIGIN !== 'https://unitrack-testing.web.app' ||
          !/^[a-fA-F0-9]{64}$/.test(env.MCP_PROBE_ENCRYPTION_KEY || '')) {
        return failure(404, 'Probe unavailable');
      }
      const token = url.searchParams.get('token');
      let payload;
      try {
        if (!token || token.length > 2048) throw new Error();
        payload = await decryptToken(token, env);
        const now = Date.now() / 1000;
        if (payload.p !== 'mcp-probe' || !['pdf', 'docx'].includes(payload.case) ||
            !Number.isInteger(payload.t) || !Number.isInteger(payload.e) ||
            payload.e - payload.t !== 120 || payload.t > now + 5 || now >= payload.e) {
          throw new Error();
        }
      } catch (_) {
        return failure(401, 'Invalid or expired test-file token');
      }
      const types = {
        pdf: 'application/pdf',
        docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
      };
      const controller = new AbortController();
      request.signal.addEventListener('abort', () => controller.abort(), {once: true});
      if (request.signal.aborted) controller.abort();
      try {
        const upstream = await fetch(
          `${env.MCP_PROBE_ORIGIN}/mcp-probe/files/${payload.case}?token=${encodeURIComponent(token)}`,
          {redirect: 'manual', signal: controller.signal, headers: {'Accept': types[payload.case]},
           cache: 'no-store'}
        );
        const contentType = (upstream.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
        if (upstream.status !== 200 || contentType !== types[payload.case] || !upstream.body) {
          await upstream.body?.cancel();
          return failure(upstream.status === 401 ? 401 : 502, 'Test document unavailable');
        }
        return new Response(upstream.body, {headers: {
          'Content-Type': types[payload.case], 'Cache-Control': 'private, no-store',
          'Content-Disposition': `inline; filename="visual-assignment.${payload.case}"`,
          'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'
        }});
      } catch (_) {
        controller.abort();
        return failure(502, 'Test document unavailable');
      }
    }

    return new Response("Not found", {status:404});
  }
};
