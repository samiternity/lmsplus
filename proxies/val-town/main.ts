export default async function (request: Request) {
  if (!await validServiceKey(request.headers.get(SERVICE_HEADER) || '', Deno.env.get('LMS_PROXY_SERVICE_KEY') || '')) {
    return privateResponse(new Response('Unauthorized', {status: 401}));
  }
  // The service key belongs to this proxy only, never to an upstream request.
  const cleanHeaders = new Headers(request.headers);
  cleanHeaders.delete(SERVICE_HEADER);
  request = new Request(request, {headers: cleanHeaders});
  if (request.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
      }
    });
  }

  const url = new URL(request.url);
  const targetUrl = url.searchParams.get('url');
  
  if (!targetUrl) {
    return new Response("Missing 'url' parameter", { status: 400 });
  }
  
  const useNativeRedirects = url.searchParams.get('native_redirects') === 'true';

  try {
    checkedUrl(targetUrl);
    if (!useNativeRedirects) {
      // Standard "Dumb" Proxy Fallback
      const newReq = new Request(targetUrl, new Request(request, { redirect: 'manual' }));
      const response = await fetchUpstream(newReq);
      const newRes = new Response(response.body, response);
      newRes.headers.set("Access-Control-Allow-Origin", "*");
      return newRes;
    } else {
      // Smart Edge-Redirect Loop
      return await nativeRedirectProxy(request, targetUrl);
    }
  } catch (err: unknown) {
    return privateResponse(new Response(err instanceof DestinationError ? err.message : 'Proxy request failed.', {status: err instanceof DestinationError ? err.status : 502}));
  }
}

// ---------------------------------------------------------------------------
// Native redirect proxy — follows the full redirect chain at the edge.
// ---------------------------------------------------------------------------
async function nativeRedirectProxy(request: Request, initialTargetUrlStr: string) {
  let currentUrl = initialTargetUrlStr;
  let currentMethod = request.method;
  let redirectCount = 0;
  const MAX_REDIRECTS = 10;

  const allSetCookies: string[] = [];
  let cookieHeader = request.headers.get("Cookie") || "";

  let isFirstRequest = true;

  while (redirectCount <= MAX_REDIRECTS) {

    let fetchReq;
    if (isFirstRequest) {
      // Stream the original body directly — avoids arrayBuffer() double-read bug
      const init: RequestInit = { redirect: 'manual' };
      if (cookieHeader) {
        const headers = new Headers(request.headers);
        headers.set("Cookie", cookieHeader);
        init.headers = headers;
      }
      fetchReq = new Request(currentUrl, new Request(request, init));
      isFirstRequest = false;
    } else {
      // Redirect method has been checked before this hop.
      const headers = new Headers();
      // Carry over safe headers from the original request
      const safeHeaders = ['accept', 'accept-language', 'user-agent', 'referer'];
      for (const h of safeHeaders) {
        const val = request.headers.get(h);
        if (val) headers.set(h, val);
      }
      if (cookieHeader) headers.set("Cookie", cookieHeader);

      fetchReq = new Request(currentUrl, {
        method: currentMethod,
        headers,
        redirect: 'manual'
      });
    }

    const response = await fetchUpstream(fetchReq);

    // Safely collect Set-Cookie headers from this hop
    // Some older environments don't support getSetCookie(), so we fallback safely
    const hopCookies = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
    if (hopCookies.length > 0) {
      allSetCookies.push(...hopCookies);
      // Merge into the running cookie string for the next hop
      const nameValues = hopCookies.map(c => c.split(';')[0].trim());
      cookieHeader = cookieHeader
        ? cookieHeader + '; ' + nameValues.join('; ')
        : nameValues.join('; ');
    }

    const status = response.status;
    if (status >= 300 && status < 400) {
      const location = response.headers.get("Location");
      if (!location) {
        return buildFinalResponse(response, allSetCookies, currentUrl);
      }
      // Resolve relative Location URLs against the current URL
      const nextUrl = checkedUrl(location, currentUrl).href;
      if (!['GET', 'HEAD'].includes(currentMethod) && status !== 303 &&
          !([301, 302].includes(status) && currentMethod === 'POST')) {
        await response.body?.cancel().catch(() => {});
        return privateResponse(new Response('Cannot replay streamed request body.', {status: 502}));
      }
      await response.body?.cancel().catch(() => {});
      currentUrl = nextUrl;
      
      // Apply redirect method rewriting without replaying a consumed stream.
      if ((status === 303 && currentMethod !== 'HEAD') || ([301, 302].includes(status) && currentMethod === 'POST')) {
        currentMethod = 'GET';
      }
      redirectCount++;
    } else {
      // Final non-redirect response
      return buildFinalResponse(response, allSetCookies, currentUrl);
    }
  }

  // Exceeded max redirects
  return privateResponse(new Response("Max redirects exceeded", {status: 502}));
}

function buildFinalResponse(finalResponse: Response, allSetCookies: string[], finalUrl: string) {
  const res = new Response(finalResponse.body, finalResponse);
  res.headers.set("Access-Control-Allow-Origin", "*");

  // Return all accumulated cookies to Python in one header (joined by |||)
  if (allSetCookies.length > 0) {
    res.headers.set("X-Proxy-Set-Cookie", allSetCookies.join("|||"));
  }

  // Return the validated final URL for the server-side caller.
  res.headers.set("X-Final-Url", finalUrl);

  return privateResponse(res);
}

const ALLOWED_ORIGINS = new Set(['https://cms.bahria.edu.pk', 'https://lms.bahria.edu.pk']);
const SERVICE_HEADER = 'X-LMS-Proxy-Key';

class DestinationError extends Error {
  status: number;
  constructor(status = 403) { super('Upstream destination is not allowed.'); this.status = status; }
}

function checkedUrl(value: string, base?: string): URL {
  let url: URL;
  try { url = new URL(value, base); } catch { throw new DestinationError(400); }
  if (url.protocol !== 'https:' || url.username || url.password || !ALLOWED_ORIGINS.has(url.origin)) {
    throw new DestinationError();
  }
  return url;
}

async function validServiceKey(supplied: string, expected: string): Promise<boolean> {
  if (!/^[0-9a-fA-F]{64}$/.test(supplied) || !/^[0-9a-fA-F]{64}$/.test(expected)) return false;
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(expected),
    {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(expected));
  return crypto.subtle.verify('HMAC', key, signature, encoder.encode(supplied));
}

function privateResponse(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.delete('X-Worker-Debug-Log');
  headers.delete(SERVICE_HEADER);
  // Fetch exposes decoded bytes; do not make the downstream client decode them again.
  headers.delete('Content-Encoding');
  headers.delete('Content-Length');
  headers.delete('Transfer-Encoding');
  headers.set('Cache-Control', 'private, no-store');
  headers.set('CDN-Cache-Control', 'no-store');
  headers.set('Netlify-CDN-Cache-Control', 'no-store');
  headers.set('Vercel-CDN-Cache-Control', 'no-store');
  return new Response(response.body, {status: response.status, statusText: response.statusText, headers});
}

async function fetchUpstream(request: Request): Promise<Response> {
  const url = checkedUrl(request.url);
  const headers = new Headers(request.headers);
  headers.delete(SERVICE_HEADER);
  headers.set('Accept-Encoding', 'identity');
  const response = await fetch(new Request(request, {headers, redirect: 'manual'}));
  const location = response.headers.get('Location');
  if (location === null) return privateResponse(response);
  let destination: URL;
  try { destination = checkedUrl(location, url.href); } catch {
    await response.body?.cancel().catch(() => {});
    throw new DestinationError(502);
  }
  const responseHeaders = new Headers(response.headers);
  responseHeaders.set('Location', destination.href);
  return privateResponse(new Response(response.body, {
    status: response.status, statusText: response.statusText, headers: responseHeaders
  }));
}
