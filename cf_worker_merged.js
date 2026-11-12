/**
 * UniTrack Unified Cloudflare Edge Worker
 * 
 * Capabilities:
 * 1. LMS Streaming & Crypto:
 *    - /download: Single assignment download with AES-GCM token decryption
 *    - /zip: Bulk ZIP streaming on-the-fly with CRC32 calculation
 *    - /upload: Assignment upload forwarder with session preservation
 * 
 * 2. Atomic Macro Engine (?macro=true):
 *    - action: "full_sync" -> Login -> Profile -> Timetable -> Attendance -> CSP -> GPA in single run
 *    - action: "attendance" -> Login -> Attendance
 *    - action: "lms_gateway" -> Login -> GoToLMS gateway
 *    - Sequential execution with 250ms anti-DDoS jitter
 *    - Preserves session cookies across CMS hops
 * 
 * 3. Transparent Proxy Fallback (?url=...):
 *    - Direct proxy or ?native_redirects=true edge redirect chain
 */

// ── LMS STREAMING / TOKEN CRYPTO UTILS ─────────────────────────────────────
const crctable = new Uint32Array(256);
for (let i = 0; i < 256; i++) {
  let c = i;
  for (let j = 0; j < 8; j++) {
    c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
  }
  crctable[i] = c;
}

function updateCrc(buffer, crc = 0) {
  crc = crc ^ -1;
  for (let i = 0; i < buffer.length; i++) {
    crc = (crc >>> 8) ^ crctable[(crc ^ buffer[i]) & 0xFF];
  }
  return (crc ^ -1) >>> 0;
}

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
  const keyHex = env.DOWNLOAD_ENCRYPTION_KEY || '';
  if (!/^[0-9a-fA-F]{64}$/.test(keyHex)) throw new Error('Download key unavailable');
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

function getRefererForUrl(targetUrl) {
  try {
    const parsed = new URL(targetUrl);
    const k = parsed.searchParams.get('k');
    if (k) {
      const b64 = decodeURIComponent(k).replace(/-/g, '+').replace(/_/g, '/');
      const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
      const decoded = atob(b64 + pad);
      const parts = decoded.split(',');
      if (parts.length >= 6 && parts[5]) {
        let ref = parts[5].trim();
        if (!ref.startsWith('http://') && !ref.startsWith('https://')) {
          ref = 'https://' + ref;
        }
        return ref;
      }
    }
  } catch (e) {}
  if (targetUrl && targetUrl.includes('LectureNotes')) {
    return 'https://lms.bahria.edu.pk/Student/LectureNotes.php';
  }
  return 'https://lms.bahria.edu.pk/Student/Assignments.php';
}

function buildHeaders(cookiesObj, targetUrl = null) {
  let cookieStr = Object.entries(cookiesObj).map(([k, v]) => `${k}=${v}`).join('; ');
  const referer = targetUrl ? getRefererForUrl(targetUrl) : "https://lms.bahria.edu.pk/Student/";
  return {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": cookieStr,
    "Accept": "*/*",
    "Referer": referer
  };
}

// ── MACRO ENGINE HELPERS ──────────────────────────────────────────────────
let cachedViewState = null;
const VIEWSTATE_TTL_MS = 60 * 60 * 1000; // 1 hour
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function extractHiddenField(html, nameOrId) {
  const re1 = new RegExp(`(?:id|name)="${nameOrId}"[^>]*value="([^"]*)"`, 'i');
  const m1 = html.match(re1);
  if (m1) return m1[1];
  const re2 = new RegExp(`value="([^"]*)"[^>]*(?:id|name)="${nameOrId}"`, 'i');
  const m2 = html.match(re2);
  if (m2) return m2[1];
  return "";
}

function getCookiesFromHeaders(headers) {
  if (typeof headers.getSetCookie === 'function') {
    return headers.getSetCookie();
  }
  const cookies = [];
  for (const [key, value] of headers.entries()) {
    if (key.toLowerCase() === 'set-cookie') {
      cookies.push(value);
    }
  }
  return cookies;
}

const LMS_ORIGIN = 'https://lms.bahria.edu.pk';
const CMS_ORIGIN = 'https://cms.bahria.edu.pk';

class UpstreamDestinationError extends Error {
  constructor(status = 403) {
    super('Upstream destination is not allowed.');
    this.status = status;
  }
}

function checkUpstreamUrl(value, lmsOnly = false, base) {
  let url;
  try {
    url = new URL(value, base);
  } catch {
    throw new UpstreamDestinationError(400);
  }
  if (url.protocol !== "https:" || url.username || url.password ||
      (url.origin !== LMS_ORIGIN && (lmsOnly || url.origin !== CMS_ORIGIN))) {
    throw new UpstreamDestinationError();
  }
  return url;
}

async function validServiceKey(supplied, expected) {
  if (!/^[0-9a-fA-F]{64}$/.test(supplied) || !/^[0-9a-fA-F]{64}$/.test(expected)) return false;
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(expected), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']
  );
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(expected));
  return crypto.subtle.verify('HMAC', key, signature, encoder.encode(supplied));
}

async function fetchUpstream(input, init, lmsOnly = false) {
  const url = checkUpstreamUrl(typeof input === 'string' ? input : input.url, lmsOnly);
  const response = await fetch(input, {...init, redirect: 'manual'});
  const location = response.headers.get('Location');
  if (location === null) return response;

  let destination;
  try {
    destination = checkUpstreamUrl(location, lmsOnly, url);
  } catch {
    await response.body?.cancel().catch(() => {});
    throw new UpstreamDestinationError(502);
  }

  // Forward the canonical URL so downstream clients cannot reinterpret the raw header.
  const headers = new Headers(response.headers);
  headers.set('Location', destination.href);
  return new Response(response.body, {status: response.status, statusText: response.statusText, headers});
}

async function getViewState(forceFresh = false) {
  const now = Date.now();
  if (!forceFresh && cachedViewState && (now - cachedViewState.timestamp < VIEWSTATE_TTL_MS)) {
    return cachedViewState;
  }

  const options = {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
  };
  let loginUrl = new URL('/Logins/Student/Login.aspx', CMS_ORIGIN);
  let res = await fetchUpstream(loginUrl.href, options);
  for (let hop = 0; [301, 302, 303, 307, 308].includes(res.status); hop++) {
    const location = res.headers.get('Location');
    if (!location) break;
    await res.body?.cancel();
    if (hop >= 20) throw new Error('Too many CMS login redirects.');
    loginUrl = checkUpstreamUrl(location, false, loginUrl);
    res = await fetchUpstream(loginUrl.href, options);
  }
  if (res.status >= 500) {
    const err = new Error(`CMS server is currently down (HTTP ${res.status})`);
    err.cmsDown = true;
    throw err;
  }
  const html = await res.text();
  if (html.includes("Runtime Error") || html.includes("Server Error in '/' Application")) {
    const err = new Error("CMS server is currently down (HTTP 500 Runtime Error)");
    err.cmsDown = true;
    throw err;
  }
  const vs = extractHiddenField(html, "__VIEWSTATE");
  const gen = extractHiddenField(html, "__VIEWSTATEGENERATOR");
  const ev = extractHiddenField(html, "__EVENTVALIDATION");

  if (!vs || !gen || !ev) {
    const err = new Error("Failed to extract ViewState from CMS login page");
    err.cmsDown = true;
    throw err;
  }

  const initialCookies = getCookiesFromHeaders(res.headers);

  cachedViewState = {
    viewState: vs,
    viewStateGen: gen,
    eventValidation: ev,
    initialCookies,
    timestamp: now
  };
  return cachedViewState;
}

function updateCookieMap(cookieMap, response, allSetCookies) {
  const hopCookies = getCookiesFromHeaders(response.headers);
  if (hopCookies.length > 0) {
    allSetCookies.push(...hopCookies);
    hopCookies.forEach(c => {
      const [keyVal] = c.split(';');
      const [k, ...vParts] = keyVal.split('=');
      if (k && k.trim()) cookieMap.set(k.trim(), vParts.join('=').trim());
    });
  }
}

function buildCookieHeader(cookieMap) {
  return Array.from(cookieMap.entries())
    .map(([k, v]) => `${k}=${v}`)
    .join('; ');
}

// ── MACRO HANDLER ─────────────────────────────────────────────────────────
async function handleMacroRequest(payload) {
  const action = payload.action;
  const username = payload.username;
  const password = payload.password;
  const instituteId = String(payload.institute_id || (username && String(username).trim().startsWith("05-") ? "5" : "2"));
  const isIpp = instituteId === "5" || (username && String(username).trim().startsWith("05-"));

  if (!action || !username || !password) {
    return new Response(JSON.stringify({ error: "Missing action, username, or password" }), {
      status: 400,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  const cookieMap = new Map();
  const allSetCookies = [];
  const debugLogs = [];

  // Helper for CMS Login
  async function attemptLogin(vs) {
    cookieMap.clear();
    allSetCookies.length = 0;

    if (vs.initialCookies && vs.initialCookies.length > 0) {
      allSetCookies.push(...vs.initialCookies);
      vs.initialCookies.forEach(c => {
        const [keyVal] = c.split(';');
        const [k, ...vParts] = keyVal.split('=');
        if (k && k.trim()) cookieMap.set(k.trim(), vParts.join('=').trim());
      });
    }

    const params = new URLSearchParams();
    params.set("__LASTFOCUS", "");
    params.set("__EVENTTARGET", "ctl00$BodyPH$btnLogin");
    params.set("__EVENTARGUMENT", "");
    params.set("__VIEWSTATE", vs.viewState);
    params.set("__VIEWSTATEGENERATOR", vs.viewStateGen);
    params.set("__EVENTVALIDATION", vs.eventValidation);
    params.set("ctl00$BodyPH$tbEnrollment", username);
    params.set("ctl00$BodyPH$tbPassword", password);
    params.set("ctl00$BodyPH$ddlInstituteID", instituteId);
    params.set("ctl00$BodyPH$ddlSubUserType", "None");
    params.set("ctl00$hfJsEnabled", "0");

    const loginHeaders = {
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      "Referer": "https://cms.bahria.edu.pk/Logins/Student/Login.aspx"
    };
    const initialCookieHeader = buildCookieHeader(cookieMap);
    if (initialCookieHeader) {
      loginHeaders["Cookie"] = initialCookieHeader;
    }

    let loginRes = await fetchUpstream("https://cms.bahria.edu.pk/Logins/Student/Login.aspx", {
      method: "POST",
      headers: loginHeaders,
      body: params.toString(),
      redirect: "manual"
    });

    updateCookieMap(cookieMap, loginRes, allSetCookies);
    debugLogs.push(`POST Login status: ${loginRes.status}`);

    let curUrl = "https://cms.bahria.edu.pk/Logins/Student/Login.aspx";
    let hops = 0;
    let hitLogoff = false;
    while (loginRes.status >= 300 && loginRes.status < 400 && hops < 5) {
      const loc = loginRes.headers.get("Location");
      if (!loc) break;
      const nextUrl = new URL(loc, curUrl).toString();
      if (nextUrl.toLowerCase().includes("logoff.aspx")) {
        hitLogoff = true;
      }
      const prevUrl = curUrl;
      curUrl = nextUrl;

      loginRes = await fetchUpstream(curUrl, {
        method: "GET",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": prevUrl,
          "Cookie": buildCookieHeader(cookieMap)
        },
        redirect: "manual"
      });
      updateCookieMap(cookieMap, loginRes, allSetCookies);
      hops++;
    }

    const isLogin = curUrl.toLowerCase().includes("login.aspx");
    if (!isLogin && loginRes.status === 200) {
      return { success: true, finalUrl: curUrl };
    }

    let isRealBadPassword = false;
    let errorText = "";
    try {
      const html = await loginRes.text();
      const msgMatch = html.match(/id=["']ctl00_BodyPH_lblMsg["'][^>]*>([\s\S]*?)<\/span>/i);
      if (msgMatch && msgMatch[1]) {
        const rawMsg = msgMatch[1].replace(/<[^>]+>/g, '').trim();
        if (rawMsg && !/maintenance|unavailable|exception|database|server error/i.test(rawMsg)) {
          isRealBadPassword = true;
          errorText = rawMsg;
        }
      }
      if (!isRealBadPassword && !hitLogoff) {
        if (/invalid\s*(?:user\s*id|login|password|credentials)/i.test(html) ||
            /incorrect\s*(?:user\s*id|login|password|credentials)/i.test(html) ||
            /user\s*(?:not\s*found|disabled|does\s*not\s*exist)/i.test(html)) {
          isRealBadPassword = true;
          errorText = "Invalid credentials";
        }
      }
    } catch (e) {
      // ignore
    }

    return {
      success: false,
      isRealBadPassword: isRealBadPassword,
      hitLogoff: hitLogoff,
      errorText: errorText,
      finalUrl: curUrl
    };
  }

  // 1. Get ViewState & perform login (always force fresh for login)
  let vs = await getViewState(true);
  let loginResult = await attemptLogin(vs);

  // If login failed due to session drop (not a verified bad password), retry once with fresh ViewState
  if (!loginResult.success && !loginResult.isRealBadPassword) {
    debugLogs.push("CMS dropped session during login, retrying once with fresh ViewState...");
    vs = await getViewState(true);
    loginResult = await attemptLogin(vs);
  }

  if (!loginResult.success) {
    if (loginResult.isRealBadPassword) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "REAL_BAD_PASSWORD",
        error: loginResult.errorText || "Invalid credentials. Please check your CMS username and password.",
        debug: debugLogs
      }), {
        status: 401,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(JSON.stringify({
      success: false,
      error_code: "SESSION_LOST",
      error: "CMS session dropped by server (Logoff.aspx or IP jitter)",
      debug: debugLogs
    }), {
      status: 502,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  // Helper for sequential page fetches with session-loss check
  async function fetchPage(targetUrl, refererUrl) {
    let curUrl = targetUrl;
    let curReferer = refererUrl;
    let res = await fetchUpstream(curUrl, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": curReferer,
        "Cookie": buildCookieHeader(cookieMap)
      },
      redirect: "manual"
    });
    updateCookieMap(cookieMap, res, allSetCookies);
    let hops = 0;
    while (res.status >= 300 && res.status < 400 && hops < 5) {
      const loc = res.headers.get("Location");
      if (!loc) break;
      const nextUrl = new URL(loc, curUrl).toString();
      curReferer = curUrl;
      curUrl = nextUrl;
      res = await fetchUpstream(curUrl, {
        method: "GET",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": curReferer,
          "Cookie": buildCookieHeader(cookieMap)
        },
        redirect: "manual"
      });
      updateCookieMap(cookieMap, res, allSetCookies);
      hops++;
    }

    if (curUrl.toLowerCase().includes("login.aspx")) {
      return { html: null, finalUrl: curUrl, status: res.status, error: "SESSION_LOST" };
    }
    const text = await res.text();
    if (text.includes("tbEnrollment")) {
      return { html: null, finalUrl: curUrl, status: res.status, error: "SESSION_LOST" };
    }
    return { html: text, finalUrl: curUrl, status: res.status };
  }

  // ── ACTION: FULL_SYNC (Atomic All-in-One Sequential Scrape) ───────────────
  if (action === "full_sync") {
    debugLogs.push("Starting full_sync atomic sequence...");
    const htmlData = {
      profile: null,
      timetable: null,
      attendance: null,
      community_service: null,
      gpa: null
    };

    // 1. Profile
    debugLogs.push("Fetching profile...");
    const profRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/Profile.aspx", loginResult.finalUrl);
    if (profRes.error === "SESSION_LOST") {
      return new Response(JSON.stringify({ success: false, error_code: "SESSION_LOST", error: "Session lost during profile fetch", debug: debugLogs }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
    htmlData.profile = profRes.html;
    await sleep(250);

    // 2. Timetable
    debugLogs.push("Fetching timetable...");
    const ttRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/CourseRegistration/TimeTable.aspx", profRes.finalUrl || loginResult.finalUrl);
    if (ttRes.error === "SESSION_LOST") {
      return new Response(JSON.stringify({ success: false, error_code: "SESSION_LOST", error: "Session lost during timetable fetch", debug: debugLogs }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
    htmlData.timetable = ttRes.html;
    await sleep(250);

    // 3. Attendance
    debugLogs.push("Fetching attendance...");
    const attRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx", ttRes.finalUrl || loginResult.finalUrl);
    if (attRes.error === "SESSION_LOST") {
      return new Response(JSON.stringify({ success: false, error_code: "SESSION_LOST", error: "Session lost during attendance fetch", debug: debugLogs }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
    htmlData.attendance = attRes.html;
    await sleep(250);

    // 4. Community Services
    let cspRes = null;
    if (isIpp) {
      debugLogs.push("Skipping community services (not applicable for IPP campus)...");
      htmlData.community_service = null;
    } else {
      debugLogs.push("Fetching community services...");
      cspRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/Exams/CommunityServices.aspx", attRes.finalUrl || loginResult.finalUrl);
      if (cspRes.error === "SESSION_LOST") {
        return new Response(JSON.stringify({ success: false, error_code: "SESSION_LOST", error: "Session lost during community service fetch", debug: debugLogs }), {
          status: 403,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }
      htmlData.community_service = cspRes.html;
      await sleep(250);
    }

    // 5. GPA / Exam Result
    debugLogs.push("Fetching exam results...");
    const gpaReferer = (cspRes && cspRes.finalUrl) || attRes.finalUrl || loginResult.finalUrl;
    const gpaRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/Exams/ExamResult.aspx", gpaReferer);
    if (gpaRes.error === "SESSION_LOST") {
      return new Response(JSON.stringify({ success: false, error_code: "SESSION_LOST", error: "Session lost during GPA fetch", debug: debugLogs }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
    htmlData.gpa = gpaRes.html;

    return new Response(JSON.stringify({
      success: true,
      html: htmlData,
      cookies: allSetCookies.join("|||"),
      debug: debugLogs
    }), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||")
      }
    });

  } else if (action === "attendance") {
    debugLogs.push("Fetching attendance page...");
    const attRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx", loginResult.finalUrl);
    if (attRes.error === "SESSION_LOST" || !attRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during attendance fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(attRes.html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
      }
    });

  } else if (action === "timetable") {
    debugLogs.push("Fetching timetable page...");
    const ttRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/CourseRegistration/TimeTable.aspx", loginResult.finalUrl);
    if (ttRes.error === "SESSION_LOST" || !ttRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during timetable fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(ttRes.html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
      }
    });

  } else if (action === "community_service" || action === "community" || action === "csp") {
    if (isIpp) {
      debugLogs.push("Community service requested for IPP student - returning not_applicable");
      return new Response(JSON.stringify({
        success: true,
        not_applicable: true,
        message: "Community service not applicable for IPP campus",
        debug: debugLogs
      }), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*"
        }
      });
    }
    debugLogs.push("Fetching community service page...");
    const cspRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/Exams/CommunityServices.aspx", loginResult.finalUrl);
    if (cspRes.error === "SESSION_LOST" || !cspRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during community service fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(cspRes.html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
      }
    });

  } else if (action === "results" || action === "gpa" || action === "exam_results" || action === "provisional_results") {
    debugLogs.push("Fetching exam results page...");
    const gpaRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/Exams/ExamResult.aspx", loginResult.finalUrl);
    if (gpaRes.error === "SESSION_LOST" || !gpaRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during exam results fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(gpaRes.html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
      }
    });

  } else if (action === "profile") {
    debugLogs.push("Fetching profile page...");
    const profRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/Profile.aspx", loginResult.finalUrl);
    if (profRes.error === "SESSION_LOST" || !profRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during profile fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(profRes.html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
      }
    });

  } else if (action === "attendance_summary") {
    debugLogs.push("Fetching attendance page for summary link extraction...");
    const attRes = await fetchPage("https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx", loginResult.finalUrl);
    if (attRes.error === "SESSION_LOST" || !attRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during attendance fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    const rawCourseCode = String(payload.course_code || "").trim();
    const cleanCode = rawCourseCode.replace(/%20/g, ' ').trim();
    const normCode = cleanCode.toLowerCase().replace(/[-\s_]+/g, '');
    let detailUrl = payload.detail_url ? String(payload.detail_url).replace(/&amp;/g, '&').trim() : "";

    if (normCode && attRes.html) {
      const trMatches = attRes.html.match(/<tr[^>]*>([\s\S]*?)<\/tr>/gi) || [];
      for (const tr of trMatches) {
        const normTr = tr.toLowerCase().replace(/[-\s_]+/g, '');
        if (normTr.includes(normCode)) {
          const linkMatch = tr.match(/href=["']?([^"'>\s]*(?:AttendanceSummary|ClassAttendanceSummary)[^"'>\s]*)["']?/i) || tr.match(/href=["']?([^"'>\s]*\?v=[^"'>\s]*)["']?/i);
          if (linkMatch && linkMatch[1]) {
            detailUrl = linkMatch[1].replace(/&amp;/g, '&');
            break;
          }
        }
      }
    }

    if (!detailUrl) {
      return new Response(JSON.stringify({
        error: `Could not find detail URL for course ${rawCourseCode}`,
        debug: debugLogs
      }), {
        status: 404,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    let fullSummaryUrl = detailUrl;
    if (!detailUrl.startsWith("http")) {
      try {
        fullSummaryUrl = new URL(detailUrl, "https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/").toString();
      } catch (e) {
        if (detailUrl.startsWith("/")) {
          fullSummaryUrl = "https://cms.bahria.edu.pk" + detailUrl;
        } else {
          fullSummaryUrl = "https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/" + detailUrl;
        }
      }
    }

    await sleep(200);
    const summaryRes = await fetchPage(fullSummaryUrl, "https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx");
    if (summaryRes.error === "SESSION_LOST" || !summaryRes.html) {
      return new Response(JSON.stringify({
        success: false,
        error_code: "SESSION_LOST",
        error: "Session lost during attendance summary fetch",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    if (summaryRes.finalUrl.toLowerCase().includes("dashboard.aspx") || /<title>[^<]*Dashboard/i.test(summaryRes.html)) {
      return new Response(JSON.stringify({
        error: "STALE_TOKEN",
        message: "Summary URL redirected to Dashboard, token invalid",
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    return new Response(summaryRes.html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
      }
    });

  } else if (action === "lms_gateway") {
    debugLogs.push("Accessing GoToLMS gateway...");
    let gwUrl = "https://cms.bahria.edu.pk/Sys/Common/GoToLMS.aspx";
    let gwRes = await fetchUpstream(gwUrl, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": loginResult.finalUrl,
        "Cookie": buildCookieHeader(cookieMap)
      },
      redirect: "manual"
    });

    updateCookieMap(cookieMap, gwRes, allSetCookies);
    let hops = 0;
    while (gwRes.status >= 300 && gwRes.status < 400 && hops < 10) {
      const loc = gwRes.headers.get("Location");
      if (!loc) break;
      const nextUrl = new URL(loc, gwUrl).toString();
      const prevUrl = gwUrl;
      gwUrl = nextUrl;

      gwRes = await fetchUpstream(gwUrl, {
        method: "GET",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": prevUrl,
          "Cookie": buildCookieHeader(cookieMap)
        },
        redirect: "manual"
      });
      updateCookieMap(cookieMap, gwRes, allSetCookies);
      hops++;
    }

    const html = await gwRes.text();
    const finalUrl = gwUrl;

    if (finalUrl.toLowerCase().includes("login.aspx")) {
      return new Response(JSON.stringify({
        error: "Gateway redirected back to login page",
        finalUrl: finalUrl,
        debug: debugLogs
      }), {
        status: 403,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    const isSurveyBlocked = finalUrl.toLowerCase().includes("qualityassurance") ||
      finalUrl.toLowerCase().includes("survey") ||
      html.includes("QualityAssuranceSurveys") ||
      html.includes("Surveys (Pending)") ||
      html.includes("Complete/evaluate the surveys first");

    if (isSurveyBlocked) {
      return new Response(JSON.stringify({
        success: false,
        survey_blocked: true,
        finalUrl: finalUrl,
        message: "CMS mandatory surveys pending before LMS access",
        debug: debugLogs
      }), {
        status: 409,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
          "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
          "X-Final-Url": finalUrl,
          "X-CMS-Survey-Blocked": "true"
        }
      });
    }

    return new Response(html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
        "X-Final-Url": finalUrl,
      }
    });

  } else if (action === "autofill_surveys") {
    debugLogs.push("Starting atomic autofill_surveys macro...");
    const evaluatedCourses = [];
    const qaBaseUrl = "https://cms.bahria.edu.pk/Sys/Student/QualityAssurance/";
    const qaListUrl = "https://cms.bahria.edu.pk/Sys/Student/QualityAssurance/QualityAssuranceSurveys.aspx";

    let lastReferer = loginResult.finalUrl;
    const MAX_SURVEYS = 20; // Safety bound
    const visitedUrls = new Set();

    for (let loopIdx = 0; loopIdx < MAX_SURVEYS; loopIdx++) {
      debugLogs.push(`Fetching QA survey list (iteration ${loopIdx + 1})...`);
      const qaPage = await fetchPage(qaListUrl, lastReferer);
      if (qaPage.error === "SESSION_LOST" || !qaPage.html) {
        return new Response(JSON.stringify({
          success: false,
          error: "Session lost while fetching survey list",
          debug: debugLogs
        }), {
          status: 403,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }
      lastReferer = qaPage.finalUrl || qaListUrl;

      // Find first pending survey link
      const rowMatches = qaPage.html.match(/<tr[^>]*>([\s\S]*?)<\/tr>/gi) || [];
      let pendingSurvey = null;

      for (const row of rowMatches) {
        if (row.includes("SurveyStudentCourseWise.aspx") || /<a[^>]+href=["'][^"']*SurveyStudentCourseWise/i.test(row)) {
          const hrefMatch = row.match(/href=["']?([^"'>\s]*SurveyStudentCourseWise\.aspx[^"'>\s]*)["']?/i);
          if (hrefMatch && hrefMatch[1]) {
            const rawHref = hrefMatch[1].replace(/&amp;/g, '&');
            let testUrl = rawHref;
            if (!testUrl.startsWith("http")) {
              try { testUrl = new URL(rawHref, qaBaseUrl).toString(); } catch (e) { testUrl = qaBaseUrl + rawHref.replace(/^\//, ''); }
            }
            if (visitedUrls.has(testUrl)) {
              continue;
            }
            const cleanTds = (row.match(/<td[^>]*>([\s\S]*?)<\/td>/gi) || []).map(td => td.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim());
            let cName = "Course Survey";
            if (cleanTds.length >= 4 && cleanTds[3]) {
              cName = cleanTds[3];
            } else if (cleanTds.length >= 3 && cleanTds[2]) {
              cName = cleanTds[2];
            }
            pendingSurvey = { name: cName, href: rawHref, fullUrl: testUrl };
            break;
          }
        }
      }

      if (!pendingSurvey) {
        debugLogs.push("No more pending surveys found.");
        break;
      }

      visitedUrls.add(pendingSurvey.fullUrl);
      let fullSurveyUrl = pendingSurvey.fullUrl;

      await sleep(200);

      const formPage = await fetchPage(fullSurveyUrl, lastReferer);
      if (formPage.error === "SESSION_LOST" || !formPage.html) {
        return new Response(JSON.stringify({
          success: false,
          error: `Session lost while loading survey for ${pendingSurvey.name}`,
          debug: debugLogs
        }), {
          status: 403,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }
      lastReferer = formPage.finalUrl || fullSurveyUrl;

      const fHtml = formPage.html;
      const vs = extractHiddenField(fHtml, "__VIEWSTATE");
      const vsGen = extractHiddenField(fHtml, "__VIEWSTATEGENERATOR");
      const ev = extractHiddenField(fHtml, "__EVENTVALIDATION");

      if (!vs) {
        continue;
      }

      // Detect submit button postback target
      let submitTarget = "ctl00$BodyPH$surveyUserControl$btnSubmit";
      const pbMatch = fHtml.match(/WebForm_DoPostBackWithOptions\(new\s+WebForm_PostBackOptions\(&quot;([^&"]*btnSubmit[^&"]*)&quot;/i)
        || fHtml.match(/WebForm_DoPostBackWithOptions\(new\s+WebForm_PostBackOptions\("([^"]*btnSubmit[^"]*)"/i);
      if (pbMatch && pbMatch[1]) {
        submitTarget = pbMatch[1];
      }

      const postParams = new URLSearchParams();
      postParams.set("__LASTFOCUS", "");
      postParams.set("__EVENTTARGET", submitTarget);
      postParams.set("__EVENTARGUMENT", "");
      postParams.set("__VIEWSTATE", vs);
      if (vsGen) postParams.set("__VIEWSTATEGENERATOR", vsGen);
      if (ev) postParams.set("__EVENTVALIDATION", ev);
      postParams.set("__SCROLLPOSITIONX", "0");
      postParams.set("__SCROLLPOSITIONY", "0");
      postParams.set("ctl00$hfJsEnabled", "0");

      // Extract all hidden inputs
      const hiddenMatches = fHtml.match(/<input[^>]+type=["']hidden["'][^>]*>/gi) || [];
      for (const hTag of hiddenMatches) {
        const nameM = hTag.match(/name=["']([^"']+)["']/i);
        const valM = hTag.match(/value=["']([^"']*)["']/i);
        if (nameM && nameM[1] && !postParams.has(nameM[1])) {
          postParams.set(nameM[1], valM ? valM[1] : "");
        }
      }

      // Group radio buttons by name
      const radioMatches = fHtml.match(/<input[^>]+type=["']radio["'][^>]*>/gi) || [];
      const radioGroups = new Map();
      for (const rTag of radioMatches) {
        const nameM = rTag.match(/name=["']([^"']+)["']/i);
        const valM = rTag.match(/value=["']([^"']*)["']/i);
        const idM = rTag.match(/id=["']([^"']+)["']/i);
        if (nameM && nameM[1]) {
          const rName = nameM[1];
          if (!radioGroups.has(rName)) radioGroups.set(rName, []);
          radioGroups.get(rName).push({
            value: valM ? valM[1] : "",
            id: idM ? idM[1] : ""
          });
        }
      }

      // Select option index 2 (Uncertain/middle) for each radio group
      for (const [rName, rOptions] of radioGroups.entries()) {
        if (rOptions.length === 0) continue;
        let chosenVal = null;
        if (rOptions.length >= 5) {
          // Standard Likert 5-point scale: index 2 is Uncertain
          chosenVal = rOptions[2].value;
        } else {
          // Midpoint fallback
          chosenVal = rOptions[Math.floor(rOptions.length / 2)].value;
        }
        postParams.set(rName, chosenVal);
      }

      // Fill all comment textareas
      const textareaMatches = fHtml.match(/<textarea[^>]*name=["']([^"']+)["'][^>]*>/gi) || [];
      for (const taTag of textareaMatches) {
        const nameM = taTag.match(/name=["']([^"']+)["']/i);
        if (nameM && nameM[1]) {
          postParams.set(nameM[1], "Good and satisfactory");
        }
      }

      // Resolve and validate the form action before sending session cookies.
      const formTagMatch = fHtml.match(/<form[^>]+action=["']([^"']+)["']/i);
      const formActionUrl = checkUpstreamUrl(
        formTagMatch?.[1] || lastReferer, false, new URL(lastReferer)
      ).href;

      let postRes = await fetchUpstream(formActionUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": lastReferer,
          "Cookie": buildCookieHeader(cookieMap)
        },
        body: postParams.toString(),
        redirect: "manual"
      });

      updateCookieMap(cookieMap, postRes, allSetCookies);
      let pHops = 0;
      let curPostUrl = formActionUrl;
      while (postRes.status >= 300 && postRes.status < 400 && pHops < 5) {
        const loc = postRes.headers.get("Location");
        if (!loc) break;
        const nextUrl = new URL(loc, curPostUrl).toString();
        const prevUrl = curPostUrl;
        curPostUrl = nextUrl;
        postRes = await fetchUpstream(curPostUrl, {
          method: "GET",
          headers: {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": prevUrl,
            "Cookie": buildCookieHeader(cookieMap)
          },
          redirect: "manual"
        });
        updateCookieMap(cookieMap, postRes, allSetCookies);
        pHops++;
      }

      lastReferer = curPostUrl;
      evaluatedCourses.push(pendingSurvey.name);
      await sleep(250);
    }

    // Now transition to GoToLMS gateway
    debugLogs.push("Transitioning to GoToLMS gateway...");
    let gwUrl = "https://cms.bahria.edu.pk/Sys/Common/GoToLMS.aspx";
    let gwRes = await fetchUpstream(gwUrl, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": lastReferer,
        "Cookie": buildCookieHeader(cookieMap)
      },
      redirect: "manual"
    });

    updateCookieMap(cookieMap, gwRes, allSetCookies);
    let gwHops = 0;
    while (gwRes.status >= 300 && gwRes.status < 400 && gwHops < 10) {
      const loc = gwRes.headers.get("Location");
      if (!loc) break;
      const nextUrl = new URL(loc, gwUrl).toString();
      const prevUrl = gwUrl;
      gwUrl = nextUrl;
      gwRes = await fetchUpstream(gwUrl, {
        method: "GET",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Referer": prevUrl,
          "Cookie": buildCookieHeader(cookieMap)
        },
        redirect: "manual"
      });
      updateCookieMap(cookieMap, gwRes, allSetCookies);
      gwHops++;
    }

    return new Response(JSON.stringify({
      success: true,
      surveys_filled: evaluatedCourses.length,
      total_evaluated: evaluatedCourses.length,
      courses: evaluatedCourses,
      lms_session_established: !gwUrl.toLowerCase().includes("login.aspx") && !gwUrl.toLowerCase().includes("qualityassurance"),
      finalUrl: gwUrl,
      cookies: allSetCookies.join("|||"),
      debug: debugLogs
    }), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "X-Proxy-Set-Cookie": allSetCookies.join("|||"),
        "X-Final-Url": gwUrl,
      }
    });

  } else {
    return new Response(JSON.stringify({ error: `Unknown action: ${action}` }), {
      status: 400,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }
}

// ── NATIVE REDIRECT PROXY ─────────────────────────────────────────────────
async function nativeRedirectProxy(request, initialTargetUrlStr) {
  let currentUrl = initialTargetUrlStr;
  let redirectCount = 0;
  const MAX_REDIRECTS = 10;

  const allSetCookies = [];
  let cookieHeader = request.headers.get("Cookie") || "";
  let isFirstRequest = true;

  while (redirectCount <= MAX_REDIRECTS) {

    let fetchReq;
    if (isFirstRequest) {
      const init = { redirect: 'manual' };
      if (cookieHeader) {
        const headers = new Headers(request.headers);
        headers.set("Cookie", cookieHeader);
        init.headers = headers;
      }
      fetchReq = new Request(currentUrl, new Request(request, init));
      isFirstRequest = false;
    } else {
      const headers = new Headers();
      const safeHeaders = ['accept', 'accept-language', 'user-agent', 'referer'];
      for (const h of safeHeaders) {
        const val = request.headers.get(h);
        if (val) headers.set(h, val);
      }
      if (cookieHeader) headers.set("Cookie", cookieHeader);

      fetchReq = new Request(currentUrl, {
        method: 'GET',
        headers,
        redirect: 'manual'
      });
    }

    const response = await fetchUpstream(fetchReq);
    const hopCookies = getCookiesFromHeaders(response.headers);
    if (hopCookies.length > 0) {
      allSetCookies.push(...hopCookies);
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
      const nextUrl = new URL(location, currentUrl).toString();
      currentUrl = nextUrl;
      redirectCount++;
    } else {
      return buildFinalResponse(response, allSetCookies, currentUrl);
    }
  }

  return new Response("Max redirects exceeded", {status: 502});
}

function buildFinalResponse(finalResponse, allSetCookies, finalUrl) {
  const res = new Response(finalResponse.body, finalResponse);
  res.headers.set("Access-Control-Allow-Origin", "*");

  if (allSetCookies.length > 0) {
    res.headers.set("X-Proxy-Set-Cookie", allSetCookies.join("|||"));
  }

  res.headers.delete("X-Worker-Debug-Log");
  res.headers.set("X-Final-Url", finalUrl);

  return res;
}

// ZIP wire format deliberately matches 7ad3f39: no data descriptors (Android Files compatibility).
const ZIP_FILE_LIMIT = 16 * 1024 * 1024;
const ZIP_BUFFER_LIMIT = 48 * 1024 * 1024;

function zipFailure(message, status, nativeForm) {
  const headers = {'Cache-Control': 'private, no-store', 'Access-Control-Allow-Origin': '*'};
  if (!nativeForm) return new Response(JSON.stringify({success: false, error: message}), {
    status, headers: {...headers, 'Content-Type': 'application/json'}
  });
  // Only generic error text crosses origins. Never include LMS URLs, cookies, or tokens.
  const data = JSON.stringify({type: 'lecture-zip-error', error: message}).replace(/</g, '\\u003c');
  return new Response(`<!doctype html><meta charset="utf-8"><p>Download failed. Return to LMS+ to retry or download individual files.</p><script>parent.postMessage(${data}, '*');</script>`, {
    status, headers: {...headers, 'Content-Type': 'text/html; charset=utf-8'}
  });
}

function checkLectureUrl(value) {
  return checkUpstreamUrl(value, true).href;
}

async function lectureFetch(fileUrl, cookies, plan, method = 'GET', extra = {}, reserve = 0) {
  let target = checkLectureUrl(fileUrl);
  for (let redirect = 0; redirect <= 3; redirect++) {
    if (plan.requests >= 45 - reserve) {
      throw Object.assign(new Error('Proxy request limit reached. Choose smaller parts or individual files.'), {code: 'request_budget'});
    }
    plan.requests++;
    const controller = new AbortController();
    const abort = () => controller.abort();
    plan.controller.signal.addEventListener('abort', abort, {once: true});
    if (plan.controller.signal.aborted) controller.abort();
    const timer = setTimeout(abort, 15000);
    let response;
    try {
      response = await fetchUpstream(target, {method, redirect: 'manual', signal: controller.signal,
        headers: {...buildHeaders(cookies, target), 'Accept-Encoding': 'identity', ...extra}}, true);
    } finally {
      clearTimeout(timer);
      // Body reads use the plan signal; retain cancellation until the entire plan ends.
    }
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      const location = response.headers.get('Location');
      if (!location) throw new Error('Invalid LMS redirect.');
      target = checkLectureUrl(new URL(location, target).href);
      continue;
    }
    return response;
  }
  throw new Error('Too many LMS redirects. Refresh LMS and retry.');
}

function sizeHeader(value) {
  if (value === null || !/^\d+$/.test(value)) return null;
  const size = Number(value);
  return Number.isSafeInteger(size) ? size : null;
}

async function prepareLectureZip(payload, request, env) {
  if (!Array.isArray(payload.u) || !payload.u.length) throw new Error('No lecture files found.');
  // One optional HEAD per file. Reserve downloads and five calls for redirects/retries.
  if (payload.u.length > 20) throw new Error('Too many files. Choose smaller parts.');
  const controller = new AbortController();
  request.signal.addEventListener('abort', () => controller.abort(), {once: true});
  if (request.signal.aborted) controller.abort();
  const plan = {controller, requests: 0, concurrency: env.ZIP_FETCH_CONCURRENCY === '3' ? 3 : 1};
  const names = new Set();
  plan.files = payload.u.map((file, index) => {
    const url = checkLectureUrl(typeof file === 'string' ? file : file.url);
    const raw = typeof file === 'string' ? new URL(url).pathname.split('/').pop() : file.name;
    const base = (raw || `file_${index + 1}`).replace(/[\\/\x00-\x1f\x7f]/g, '_').replace(/^\.+/, '_');
    let name = base;
    for (let i = 2; names.has(name); i++) {
      const dot = base.lastIndexOf('.');
      name = dot > 0 ? `${base.slice(0, dot)}_${i}${base.slice(dot)}` : `${base}_${i}`;
    }
    names.add(name);
    if (new TextEncoder().encode(name).length > 65535) throw new Error('Lecture filename too long.');
    return {url, name, size: null};
  });
  let cursor = 0;
  try {
    await Promise.all(Array.from({length: Math.min(3, plan.files.length)}, async () => {
      while (cursor < plan.files.length) {
        const file = plan.files[cursor++];
        let response;
        try {
          response = await lectureFetch(file.url, payload.c, plan, 'HEAD', {}, plan.files.length + 5);
          if ([401, 403].includes(response.status)) throw new Error('LMS session expired. Refresh LMS and retry.');
          const encoded = response.headers.get('Content-Encoding');
          if (response.ok && (!encoded || encoded === 'identity')) file.size = sizeHeader(response.headers.get('Content-Length'));
        } catch (error) {
          // Metadata is optional. Preserve download capacity and tolerate unsupported HEAD requests.
          if (error.code !== 'request_budget' && error.name !== 'AbortError' && !(error instanceof TypeError)) throw error;
        } finally {
          await response?.body?.cancel();
        }
        if (file.size > ZIP_FILE_LIMIT) throw new Error('Lecture exceeds proxy memory limit. Download this file individually.');
      }
    }));
  } catch (error) { controller.abort(); throw error; }
  // Extension sniffing may change names. Omit a total unless every filename is already final.
  plan.total = plan.files.every(file => file.size !== null && /\.[a-zA-Z0-9]{2,5}$/.test(file.name))
    ? 22 + plan.files.reduce((sum, file) => sum + file.size + 76 + 2 * new TextEncoder().encode(file.name).length, 0)
    : null;
  if (plan.total !== null && plan.total >= 0xffffffff) throw new Error('Archive exceeds ZIP32 limits. Download individual files.');
  return plan;
}

async function loadLectureFile(file, cookies, plan) {
  for (let attempt = 0; attempt < 2; attempt++) {
    let response;
    try {
      response = await lectureFetch(file.url, cookies, plan);
      if (response.status === 429 || response.status >= 500) {
        const retryAfter = response.headers.get('Retry-After');
        const seconds = retryAfter === null ? NaN : Number(retryAfter);
        const wait = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryAfter) - Date.now();
        const delay = Math.min(5000, Math.max(0, Number.isFinite(wait) ? wait : 500));
        await response.body?.cancel();
        plan.concurrency = 1;
        if (attempt === 0) { await sleep(delay); continue; }
      }
      if (!response.ok) throw new Error(`LMS file request failed (${response.status}). Refresh LMS or download individually.`);
      const ct = (response.headers.get('Content-Type') || '').toLowerCase();
      if (ct.includes('text/html') || ct.includes('text/php')) throw new Error('LMS returned a login page. Refresh LMS and retry.');
      const reader = response.body?.getReader();
      const chunks = [];
      let length = 0;
      if (reader) {
        try {
          while (true) {
            let timer;
            const result = await Promise.race([reader.read(), new Promise((_, reject) => {
              timer = setTimeout(() => reject(Object.assign(new Error('LMS file transfer timed out.'), {retryable: true})), 30000);
            })]).finally(() => clearTimeout(timer));
            if (result.done) break;
            length += result.value.length;
            if (length > ZIP_FILE_LIMIT) throw new Error('Lecture exceeds proxy memory limit. Download this file individually.');
            chunks.push(result.value);
          }
        } catch (error) { await reader.cancel().catch(() => {}); throw error; }
        finally { reader.releaseLock(); }
      }
      if (file.size !== null && length !== file.size) throw new Error('Lecture size changed. Retry the download.');
      // Consolidate chunks and release their references immediately. Allow headroom for this transient copy.
      const fileBytes = new Uint8Array(length);
      let offset = 0;
      for (let i = 0; i < chunks.length; i++) { fileBytes.set(chunks[i], offset); offset += chunks[i].length; chunks[i] = null; }
      const sample = new TextDecoder().decode(fileBytes.subarray(0, 256)).trim().toLowerCase();
      if (sample.startsWith('<!doctype') || sample.startsWith('<html') || sample.includes('<head>') || sample.includes('<body>')) {
        throw new Error('LMS returned a login page. Refresh LMS and retry.');
      }
      return {fileBytes, headers: response.headers};
    } catch (error) {
      await response?.body?.cancel().catch(() => {});
      if (attempt || plan.controller.signal.aborted || !(error instanceof TypeError || error.name === 'AbortError' || error.retryable)) throw error;
    }
  }
}

// ── MAIN WORKER ROUTER ────────────────────────────────────────────────────
// Dedicated read-only connector path; legacy browser download tokens cannot use it.
async function mcpAssignmentDownload(request, env) {
  const fail = (status, error) => new Response(JSON.stringify({error}), {
    status, headers: {'Content-Type': 'application/json', 'Cache-Control': 'private, no-store'}
  });
  if (!/^[a-fA-F0-9]{64}$/.test(env.LMS_MCP_DOWNLOAD_KEY || '')) return fail(404, 'Unavailable');
  if (request.method !== 'POST') return fail(405, 'POST required');
  let payload;
  try {
    const reader = request.body?.getReader();
    if (!reader) return fail(400, 'Invalid request');
    const chunks = [];
    let length = 0;
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > 16384) { await reader.cancel(); return fail(413, 'Request too large'); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    const token = new URLSearchParams(new TextDecoder().decode(body)).get('token');
    if (!token || token.length > 14000) throw new Error();
    const raw = base64UrlToBytes(token);
    const key = await crypto.subtle.importKey('raw', hexToBytes(env.LMS_MCP_DOWNLOAD_KEY),
                                               {name: 'AES-GCM'}, false, ['decrypt']);
    const plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: raw.slice(0, 12),
      additionalData: new TextEncoder().encode('mcp-download-v1')}, key, raw.slice(12));
    payload = JSON.parse(new TextDecoder().decode(plain));
    const now = Date.now() / 1000;
    if (payload.purpose !== 'mcp-assignment-download' || !Number.isInteger(payload.issued) ||
        !Number.isInteger(payload.expires) || payload.expires - payload.issued !== 120 ||
        payload.issued > now + 5 || now >= payload.expires ||
        typeof payload.cookies !== 'object' || !payload.cookies || Array.isArray(payload.cookies) ||
        Object.keys(payload.cookies).length > 50) throw new Error();
    const target = new URL(payload.url);
    if (target.protocol !== 'https:' || target.hostname !== 'lms.bahria.edu.pk' ||
        target.port || target.username || target.password || target.hash) throw new Error();
    for (const [name, value] of Object.entries(payload.cookies)) {
      if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || typeof value !== 'string' || /[\r\n;]/.test(value)) throw new Error();
    }
  } catch (_) { return fail(401, 'Invalid or expired download authorization'); }

  const abort = new AbortController();
  const cancel = () => abort.abort();
  request.signal.addEventListener('abort', cancel, {once: true});
  if (request.signal.aborted) abort.abort();
  const timer = setTimeout(cancel, 20000);
  const cleanup = () => { clearTimeout(timer); request.signal.removeEventListener('abort', cancel); };
  let upstream, reader;
  try {
    upstream = await fetchUpstream(payload.url, {headers: {...buildHeaders(payload.cookies, payload.url),
      'Accept-Encoding': 'identity'}, redirect: 'manual', signal: abort.signal}, true);
    if ((upstream.status >= 300 && upstream.status < 400) || [401, 403].includes(upstream.status) ||
        /text\/(html|php)/i.test(upstream.headers.get('Content-Type') || '')) {
      await upstream.body?.cancel(); cleanup(); return fail(401, 'University authentication required');
    }
    if (!upstream.ok) {
      await upstream.body?.cancel(); cleanup(); return fail(upstream.status === 404 ? 404 : 502, 'University file unavailable');
    }
    const limit = 10 * 1024 * 1024;
    if (Number(upstream.headers.get('Content-Length') || 0) > limit) {
      await upstream.body?.cancel(); cleanup(); return fail(413, 'File exceeds processing limit');
    }
    reader = upstream.body.getReader();
    const initial = [];
    let count = 0;
    while (count < 8) {
      const {value, done} = await reader.read();
      if (done) break;
      count += value.byteLength;
      if (count > limit) throw new Error();
      initial.push(value);
    }
    const prefix = new Uint8Array(Math.min(count, 8));
    let offset = 0;
    for (const chunk of initial) {
      const part = chunk.subarray(0, prefix.length - offset); prefix.set(part, offset); offset += part.length;
      if (offset === prefix.length) break;
    }
    const magic = new TextDecoder().decode(prefix);
    if (!magic.startsWith('%PDF-') && !(prefix[0] === 0x50 && prefix[1] === 0x4b && prefix[2] === 3 && prefix[3] === 4)) {
      await reader.cancel(); cleanup(); return fail(/^\s*</.test(magic) ? 401 : 415, 'Unsupported university file');
    }
    const body = new ReadableStream({
      async pull(controller) {
        try {
          if (initial.length) { controller.enqueue(initial.shift()); return; }
          const {value, done} = await reader.read();
          if (done) { cleanup(); reader.releaseLock(); controller.close(); return; }
          count += value.byteLength;
          if (count > limit) throw new Error('File exceeds processing limit');
          controller.enqueue(value);
        } catch (_) { abort.abort(); await reader.cancel().catch(() => {}); cleanup(); controller.error(new Error('File transfer failed')); }
      },
      async cancel() { abort.abort(); await reader.cancel().catch(() => {}); cleanup(); }
    });
    return new Response(body, {headers: {'Content-Type': 'application/octet-stream',
      'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}});
  } catch (error) {
    abort.abort(); if (reader) await reader.cancel().catch(() => {}); cleanup();
    if (error instanceof UpstreamDestinationError && error.status === 502) return fail(401, 'University authentication required');
    return fail(502, 'University file transfer failed');
  }
}

export default {
  async fetch(request, env, ctx) {
    if (env.LMS_MCP_PRIVATE_PROXY === 'true') {
      const supplied = request.headers.get('X-LMS-MCP-Proxy-Key') || '';
      if (!/^[a-f0-9]{64}$/.test(supplied) || !/^[a-fA-F0-9]{64}$/.test(env.LMS_MCP_DOWNLOAD_KEY || '')) {
        return new Response('Unauthorized', {status: 401});
      }
      const expected = new Uint8Array(await crypto.subtle.digest('SHA-256',
        new TextEncoder().encode('mcp-proxy:' + env.LMS_MCP_DOWNLOAD_KEY.toLowerCase())));
      const candidate = hexToBytes(supplied);
      let difference = 0;
      for (let index = 0; index < expected.length; index++) difference |= expected[index] ^ candidate[index];
      if (difference !== 0) return new Response('Unauthorized', {status: 401});
      const headers = new Headers(request.headers);
      headers.delete('X-LMS-MCP-Proxy-Key');
      request = new Request(request, {headers});
    }
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
          "Access-Control-Allow-Headers": "*",
          "Access-Control-Max-Age": "86400",
        }
      });
    }

    const url = new URL(request.url);

    if (url.pathname === '/mcp-download') return mcpAssignmentDownload(request, env);

    // 1. Assignment Download & Bulk ZIP Stream
    if (url.pathname === '/download' || url.pathname === '/zip' || url.pathname === '/bulk-zip') {
      const corsHeaders = { "Access-Control-Allow-Origin": "*" };
      const nativeForm = request.method === 'POST';
      let form;
      if (nativeForm) {
        if (Number(request.headers.get('Content-Length') || 0) > 262144) return zipFailure('Download request too large.', 413, true);
        const reader = request.body?.getReader();
        const decoder = new TextDecoder();
        let body = '', bytes = 0;
        if (reader) {
          try {
            while (true) {
              const result = await reader.read();
              if (result.done) break;
              bytes += result.value.byteLength;
              if (bytes > 262144) {
                await reader.cancel();
                return zipFailure('Download request too large.', 413, true);
              }
              body += decoder.decode(result.value, {stream: true});
            }
            body += decoder.decode();
          } finally { reader.releaseLock(); }
        }
        form = new URLSearchParams(body);
      }
      const token = form?.get('token') || url.searchParams.get('token');
      if (!token) return zipFailure("Missing token", 400, nativeForm);

      let payload;
      try {
        payload = await decryptToken(token, env);
      } catch (e) {
        return zipFailure("Invalid or expired token", 401, nativeForm);
      }

      const ageSeconds = (Date.now() / 1000) - payload.t;
      if (!Number.isFinite(ageSeconds) || ageSeconds < -60 || ageSeconds > 900) {
        return zipFailure("Token expired. Refresh LMS and retry.", 401, nativeForm);
      }

      const isZip = url.pathname === '/zip' || url.pathname === '/bulk-zip';
      
      if (!isZip && nativeForm) {
        const controller = new AbortController();
        request.signal.addEventListener('abort', () => controller.abort(), {once: true});
        if (request.signal.aborted) controller.abort();
        try {
          if (!Array.isArray(payload.u) || payload.u.length !== 1) throw new Error('Invalid individual download.');
          const file = payload.u[0];
          const target = typeof file === 'string' ? file : file.url;
          const upstream = await lectureFetch(target, payload.c, {controller, requests: 0});
          const ct = (upstream.headers.get('Content-Type') || '').toLowerCase();
          if (!upstream.ok || ct.includes('text/html') || ct.includes('text/php')) {
            await upstream.body?.cancel();
            throw new Error('LMS file unavailable. Refresh LMS and retry.');
          }
          const filename = (form.get('filename') || file.name || 'lecture').replace(/["\\/\x00-\x1f\x7f]/g, '_');
          const headers = new Headers(upstream.headers);
          headers.delete('Set-Cookie');
          headers.delete('X-Worker-Debug-Log');
          headers.set('Cache-Control', 'private, no-store');
          headers.set('Access-Control-Allow-Origin', '*');
          headers.set('Content-Disposition', `attachment; filename="${filename.replace(/[^\x20-\x7e]/g, '_')}"; filename*=UTF-8''${encodeURIComponent(filename)}`);
          return new Response(upstream.body, {headers});
        } catch (error) {
          controller.abort();
          return zipFailure(error.message || 'Individual download failed.', 502, true);
        }
      }
      if (!isZip) {
        let fetchRes;
        try {
          const target = payload.u?.[0];
          const targetUrl = checkLectureUrl(typeof target === 'string' ? target : target?.url);
          fetchRes = await fetchUpstream(targetUrl, {headers: buildHeaders(payload.c, targetUrl)}, true);
        } catch (error) {
          if (error instanceof UpstreamDestinationError && error.status === 502) {
            return new Response("LMS Session Expired. Please refresh your dashboard.", {status: 401, headers: corsHeaders});
          }
          return zipFailure("LMS file unavailable. Refresh LMS and retry.", error instanceof UpstreamDestinationError ? error.status : 502, false);
        }

        if (fetchRes.status >= 300 && fetchRes.status < 400) {
          return new Response("LMS Session Expired. Please refresh your dashboard.", { status: 401, headers: corsHeaders });
        }

        const newRes = new Response(fetchRes.body, fetchRes);
        newRes.headers.set("Access-Control-Allow-Origin", "*");
        newRes.headers.delete("Set-Cookie");
        newRes.headers.delete("X-Worker-Debug-Log");
        return newRes;
      } else {
        const zipFilename = form?.get('filename') || url.searchParams.get('filename') || "download.zip";
        
        let plan;
        try {
          plan = await prepareLectureZip(payload, request, env);
        } catch (error) {
          return zipFailure(error.message, error.status || 502, nativeForm);
        }
        const { readable, writable } = plan.total === null ? new TransformStream() : new FixedLengthStream(plan.total);
        const writer = writable.getWriter();
        writer.closed.catch(() => plan.controller.abort());

        const processZip = async () => {
          const files = plan.files;
          const textEncoder = new TextEncoder();
          const centralDirectory = [];
          const emittedNames = new Set();
          let offset = 0;

          // Fetch ahead only within the reserved payload budget; retain Android-safe local headers.
          const pending = new Map();
          let next = 0;
          const schedule = () => {
            let reserved = [...pending.keys()].reduce((n, i) => n + (files[i].size ?? ZIP_FILE_LIMIT), 0);
            while (next < files.length && pending.size < plan.concurrency) {
              const file = files[next];
              const reserve = file.size ?? ZIP_FILE_LIMIT;
              if (reserved + reserve > ZIP_BUFFER_LIMIT) break;
              if (file.size === null && pending.size) break;
              const index = next++;
              // Resolve errors as values so a later entry cannot create an unhandled rejection.
              pending.set(index, loadLectureFile(file, payload.c, plan).catch(error => ({error})));
              reserved += reserve;
              if (file.size === null) break;
            }
          };
          schedule();
          for (let index = 0; index < files.length; index++) {
            const file = files[index];
            try {
              const loaded = await pending.get(index);
              if (loaded.error) throw loaded.error;
              const {fileBytes, headers} = loaded;
              const fileUrl = file.url;
              const fileName = file.name;
              const fetchRes = {headers};
              const fileSize = fileBytes.length;
              const fileCrc = updateCrc(fileBytes);
              // Check if fileName has an extension. If not, inspect LMS response headers or sniff magic bytes
              let finalFileName = fileName;
              if (!/\.[a-zA-Z0-9]{2,5}$/.test(finalFileName)) {
                let detectedExt = "";
                const cd = fetchRes.headers.get("Content-Disposition") || "";
                const cdMatch = cd.match(/filename[^;=\n]*=(["']?)([^"';\n]+)\1/i);
                if (cdMatch && cdMatch[2]) {
                  const m = cdMatch[2].match(/\.[a-zA-Z0-9]{2,5}$/);
                  if (m) detectedExt = m[0].toLowerCase();
                }
                if (!detectedExt) {
                  const ct = (fetchRes.headers.get("Content-Type") || "").toLowerCase();
                  if (ct.includes("pdf")) detectedExt = ".pdf";
                  else if (ct.includes("presentation") || ct.includes("powerpoint")) detectedExt = ".pptx";
                  else if (ct.includes("wordprocessing") || ct.includes("msword")) detectedExt = ".docx";
                  else if (ct.includes("spreadsheet") || ct.includes("excel")) detectedExt = ".xlsx";
                  else if (ct.includes("zip")) detectedExt = ".zip";
                }
                if (!detectedExt && fileBytes.length >= 4) {
                  // Magic bytes detection
                  if (fileBytes[0] === 0x25 && fileBytes[1] === 0x50 && fileBytes[2] === 0x44 && fileBytes[3] === 0x46) {
                    detectedExt = ".pdf";
                  } else if (fileBytes[0] === 0x50 && fileBytes[1] === 0x4B && fileBytes[2] === 0x03 && fileBytes[3] === 0x04) {
                    const headerChunk = new TextDecoder("latin1").decode(fileBytes.subarray(0, Math.min(1500, fileBytes.length)));
                    if (headerChunk.includes("ppt/")) detectedExt = ".pptx";
                    else if (headerChunk.includes("word/")) detectedExt = ".docx";
                    else if (headerChunk.includes("xl/")) detectedExt = ".xlsx";
                    else detectedExt = ".zip";
                  } else if (fileBytes[0] === 0xD0 && fileBytes[1] === 0xCF && fileBytes[2] === 0x11 && fileBytes[3] === 0xE0) {
                    const headerChunk = new TextDecoder("latin1").decode(fileBytes.subarray(0, Math.min(2048, fileBytes.length)));
                    if (headerChunk.includes("PowerPoint")) detectedExt = ".ppt";
                    else if (headerChunk.includes("WordDocument")) detectedExt = ".doc";
                    else if (headerChunk.includes("Workbook") || headerChunk.includes("Book")) detectedExt = ".xls";
                    else detectedExt = ".ppt";
                  }
                }
                if (detectedExt) {
                  finalFileName = `${finalFileName}${detectedExt}`;
                }
              }

              const base = finalFileName;
              for (let n = 2; emittedNames.has(finalFileName); n++) {
                const dot = base.lastIndexOf('.');
                finalFileName = dot > 0 ? `${base.slice(0, dot)}_${n}${base.slice(dot)}` : `${base}_${n}`;
              }
              emittedNames.add(finalFileName);
              const nameBytes = textEncoder.encode(finalFileName);
              if (nameBytes.length > 65535 || offset + 30 + nameBytes.length + fileSize >= 0xffffffff) {
                throw new Error('Archive exceeds ZIP32 limits. Download individual files.');
              }
              const timeDate = 0x00210000; // Jan 1, 1980 DOS timestamp

              const lfh = new Uint8Array(30 + nameBytes.length);
              const lfhV = new DataView(lfh.buffer);
              lfhV.setUint32(0, 0x04034b50, true);
              lfhV.setUint16(4, 20, true);
              lfhV.setUint16(6, 0x0800, true); // bit 11 only (UTF-8, bit 3 = 0)
              lfhV.setUint16(8, 0, true);
              lfhV.setUint32(10, timeDate, true);
              lfhV.setUint32(14, fileCrc, true);
              lfhV.setUint32(18, fileSize, true);
              lfhV.setUint32(22, fileSize, true);
              lfhV.setUint16(26, nameBytes.length, true);
              lfh.set(nameBytes, 30);

              await writer.write(lfh);
              
              const headerOffset = offset;
              offset += lfh.length;

              await writer.write(fileBytes);
              offset += fileSize;

              centralDirectory.push({
                nameBytes, fileCrc, fileSize, headerOffset, timeDate
              });
              pending.delete(index);
              schedule();
            } catch (fileErr) {
              plan.controller.abort();
              throw fileErr;
            }
          }

          if (centralDirectory.length === 0) {
            throw new Error("No lecture files could be retrieved from LMS. LMS session may have expired.");
          }

          const cdStart = offset;
          for (const cdFile of centralDirectory) {
            const cdh = new Uint8Array(46 + cdFile.nameBytes.length);
            const cdhV = new DataView(cdh.buffer);
            cdhV.setUint32(0, 0x02014b50, true);
            cdhV.setUint16(4, 20, true);
            cdhV.setUint16(6, 20, true);
            cdhV.setUint16(8, 0x0800, true); // bit 11 only (UTF-8, bit 3 = 0)
            cdhV.setUint16(10, 0, true);
            cdhV.setUint32(12, cdFile.timeDate, true);
            cdhV.setUint32(16, cdFile.fileCrc, true);
            cdhV.setUint32(20, cdFile.fileSize, true);
            cdhV.setUint32(24, cdFile.fileSize, true);
            cdhV.setUint16(28, cdFile.nameBytes.length, true);
            cdhV.setUint32(42, cdFile.headerOffset, true);
            cdh.set(cdFile.nameBytes, 46);
            
            await writer.write(cdh);
            offset += cdh.length;
          }

          const cdSize = offset - cdStart;

          const eocd = new Uint8Array(22);
          const eocdV = new DataView(eocd.buffer);
          eocdV.setUint32(0, 0x06054b50, true);
          eocdV.setUint16(8, centralDirectory.length, true);
          eocdV.setUint16(10, centralDirectory.length, true);
          eocdV.setUint32(12, cdSize, true);
          eocdV.setUint32(16, cdStart, true);
          
          await writer.write(eocd);
          await writer.close();
        };

        processZip().catch(err => {
          console.error("ZIP streaming failed", {name: err?.name || "Cancelled"});
          plan.controller.abort();
          writer.abort(err).catch(() => {});
        });

        const safeCleanFilename = zipFilename.replace(/["\\/\x00-\x1f\x7f]/g, '_').trim() || "download.zip";
        const asciiFilename = safeCleanFilename.replace(/[^\x20-\x7E]/g, '_');
        return new Response(readable, {
          headers: {
            "Content-Type": "application/zip",
            "Content-Disposition": `attachment; filename="${asciiFilename}"; filename*=UTF-8''${encodeURIComponent(safeCleanFilename)}`,
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "private, no-store"
          }
        });
      }
    }

    // 2. Assignment Upload Forwarder
    if (url.pathname === '/upload') {
      if (request.method !== 'POST') return new Response('POST required', {status: 405});
      const token = url.searchParams.get('token');
      if (!token) return new Response("Missing token", { status: 400 });

      let payload;
      try {
        payload = await decryptToken(token, env);
      } catch (e) {
        return new Response("Invalid or expired token", { status: 401 });
      }

      const ageSeconds = (Date.now() / 1000) - payload.t;
      if (!Number.isFinite(ageSeconds) || ageSeconds < -60 || ageSeconds > 900) {
        return new Response("Token expired", { status: 401 });
      }

      let fetchRes;
      try {
        const target = typeof payload.u === 'string' ? payload.u : payload.u?.[0];
        const targetUrl = checkLectureUrl(target);
        const headers = buildHeaders(payload.c);
        const contentType = request.headers.get("Content-Type");
        if (contentType) headers["Content-Type"] = contentType;
        const contentLength = request.headers.get("Content-Length");
        if (contentLength) headers["Content-Length"] = contentLength;

        fetchRes = await fetchUpstream(targetUrl, {
          method: 'POST',
          headers,
          body: request.body,
          duplex: 'half'
        }, true);
      } catch (error) {
        return new Response("LMS upload unavailable.", {
          status: error instanceof UpstreamDestinationError ? error.status : 502,
          headers: {"Access-Control-Allow-Origin": "*"}
        });
      }

      if (fetchRes.status >= 300 && fetchRes.status < 400) {
        const location = fetchRes.headers.get("Location") || "";
        let success = true;
        let error = "";
        
        const isSuccessRedirect = location.toLowerCase().includes("success");
        if (!isSuccessRedirect && (location.toLowerCase().includes("failure") || location.toLowerCase().includes("error") || location.includes("&r="))) {
          success = false;
          const match = location.match(/&r=([^&]+)/);
          error = match ? decodeURIComponent(match[1]) : "LMS rejected the upload";
        }

        return new Response(JSON.stringify({ success, status: fetchRes.status, error }), {
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
          }
        });
      }
      
      const newRes = new Response(fetchRes.body, fetchRes);
      newRes.headers.set("Access-Control-Allow-Origin", "*");
      newRes.headers.delete("Set-Cookie");
      newRes.headers.delete("X-Worker-Debug-Log");
      return newRes;
    }

    const supplied = request.headers.get('X-LMS-Proxy-Key') || '';
    if (env.LMS_MCP_PRIVATE_PROXY !== 'true' &&
        !await validServiceKey(supplied, env.LMS_PROXY_SERVICE_KEY || '')) {
      return new Response('Unauthorized', {status: 401});
    }
    if (request.headers.has('X-LMS-Proxy-Key')) {
      const headers = new Headers(request.headers);
      headers.delete('X-LMS-Proxy-Key');
      request = new Request(request, {headers});
    }

    // 3. Atomic Macro Execution (?macro=true)
    if (url.searchParams.get("macro") === "true" || request.headers.get("X-Proxy-Macro") === "true") {
      try {
        const payload = await request.json();
        return await handleMacroRequest(payload);
      } catch (err) {
        if (err instanceof UpstreamDestinationError) {
          return new Response(JSON.stringify({error: err.message, cms_down: false}), {
            status: err.status, headers: {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}
          });
        }
        const isCmsDown = Boolean(err.cmsDown || err.message.includes("CMS server is currently down") || err.message.includes("ViewState") || err.message.includes("Runtime Error"));
        return new Response(JSON.stringify({ error: err.message, cms_down: isCmsDown }), {
          status: isCmsDown ? 503 : 500,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }
    }

    // 4. Default Transparent Proxy Fallback (?url=...)
    const targetUrl = url.searchParams.get('url');
    if (!targetUrl) return new Response("Missing 'url' or 'token'", { status: 400 });
    
    const useNativeRedirects = url.searchParams.get('native_redirects') === 'true';

    try {
      const checkedTarget = checkUpstreamUrl(targetUrl).href;
      if (!useNativeRedirects) {
        const newReq = new Request(checkedTarget, new Request(request, { redirect: 'manual' }));
        const response = await fetchUpstream(newReq);
        const newRes = new Response(response.body, response);
        newRes.headers.delete("X-Worker-Debug-Log");
        newRes.headers.set("Access-Control-Allow-Origin", "*");
        return newRes;
      } else {
        return await nativeRedirectProxy(request, checkedTarget);
      }
    } catch (err) {
      return err instanceof UpstreamDestinationError
        ? new Response(err.message, {status: err.status})
        : new Response("Upstream request failed.", {status: 502});
    }
  }
};
