import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

interface ViewStateCache {
  viewState: string;
  viewStateGen: string;
  eventValidation: string;
  initialCookies?: string[];
  timestamp: number;
}

let cachedViewState: ViewStateCache | null = null;
const VIEWSTATE_TTL_MS = 60 * 60 * 1000; // 1 hour

function extractHiddenField(html: string, nameOrId: string): string {
  const re1 = new RegExp(`(?:id|name)="${nameOrId}"[^>]*value="([^"]*)"`, 'i');
  const m1 = html.match(re1);
  if (m1) return m1[1];
  const re2 = new RegExp(`value="([^"]*)"[^>]*(?:id|name)="${nameOrId}"`, 'i');
  const m2 = html.match(re2);
  if (m2) return m2[1];
  return "";
}

const LMS_ORIGIN = 'https://lms.bahria.edu.pk';
const CMS_ORIGIN = 'https://cms.bahria.edu.pk';

class UpstreamDestinationError extends Error {
  status: number;

  constructor(status = 403) {
    super('Upstream destination is not allowed.');
    this.status = status;
  }
}

function checkUpstreamUrl(value: string, lmsOnly = false, base?: URL) {
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

async function validServiceKey(supplied: string, expected: string) {
  if (!/^[0-9a-fA-F]{64}$/.test(supplied) || !/^[0-9a-fA-F]{64}$/.test(expected)) return false;
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(expected), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']
  );
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(expected));
  return crypto.subtle.verify('HMAC', key, signature, encoder.encode(supplied));
}

async function fetchUpstream(input: string | Request, init?: RequestInit, lmsOnly = false) {
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

async function getViewState(forceFresh = false): Promise<ViewStateCache> {
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
    const err: any = new Error(`CMS server is currently down (HTTP ${res.status})`);
    err.cmsDown = true;
    throw err;
  }
  const html = await res.text();
  if (html.includes("Runtime Error") || html.includes("Server Error in '/' Application")) {
    const err: any = new Error("CMS server is currently down (HTTP 500 Runtime Error)");
    err.cmsDown = true;
    throw err;
  }
  const vs = extractHiddenField(html, "__VIEWSTATE");
  const gen = extractHiddenField(html, "__VIEWSTATEGENERATOR");
  const ev = extractHiddenField(html, "__EVENTVALIDATION");

  if (!vs || !gen || !ev) {
    const err: any = new Error("Failed to extract ViewState from CMS login page");
    err.cmsDown = true;
    throw err;
  }

  let initialCookies: string[] = [];
  if (typeof (res.headers as any).getSetCookie === 'function') {
    initialCookies = (res.headers as any).getSetCookie();
  } else {
    for (const [key, value] of res.headers.entries()) {
      if (key.toLowerCase() === 'set-cookie') {
        initialCookies.push(value);
      }
    }
  }

  cachedViewState = {
    viewState: vs,
    viewStateGen: gen,
    eventValidation: ev,
    initialCookies,
    timestamp: now
  };
  return cachedViewState;
}

function updateCookieMap(cookieMap: Map<string, string>, response: Response, allSetCookies: string[]) {
  let hopCookies: string[] = [];
  if (typeof response.headers.getSetCookie === 'function') {
    hopCookies = response.headers.getSetCookie();
  } else {
    for (const [key, value] of response.headers.entries()) {
      if (key.toLowerCase() === 'set-cookie') {
        hopCookies.push(value);
      }
    }
  }

  if (hopCookies.length > 0) {
    allSetCookies.push(...hopCookies);
    hopCookies.forEach(c => {
      const [keyVal] = c.split(';');
      const [k, ...vParts] = keyVal.split('=');
      if (k && k.trim()) cookieMap.set(k.trim(), vParts.join('=').trim());
    });
  }
}

function buildCookieHeader(cookieMap: Map<string, string>): string {
  return Array.from(cookieMap.entries())
    .map(([k, v]) => `${k}=${v}`)
    .join('; ');
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// ── MACRO HANDLER ─────────────────────────────────────────────────────────
async function handleMacroRequest(payload: any): Promise<Response> {
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

  const cookieMap = new Map<string, string>();
  const allSetCookies: string[] = [];
  const debugLogs: string[] = [];

  interface LoginAttemptResult {
    success: boolean;
    finalUrl: string;
    isRealBadPassword: boolean;
    hitLogoff: boolean;
    errorText: string;
  }

  // Helper for CMS Login
  async function attemptLogin(vs: ViewStateCache): Promise<LoginAttemptResult> {
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

    const loginHeaders: Record<string, string> = {
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
      return { success: true, finalUrl: curUrl, isRealBadPassword: false, hitLogoff, errorText: "" };
    }

    let isRealBadPassword = false;
    let errorText = "";
    try {
      const html = await loginRes.text();
      const msgMatch = html.match(/id=["']ctl00_BodyPH_lblMsg["'][^>]*>([\s\S]*?)<\/span>/i);
      if (msgMatch && msgMatch[1]) {
        const rawMsg = msgMatch[1].replace(/<[^>]+>/g, "").trim();
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
    }

    return {
      success: false,
      isRealBadPassword,
      hitLogoff,
      errorText,
      finalUrl: curUrl
    };
  }

  // 1. Get ViewState & perform login (always force fresh for login)
  let vs = await getViewState(true);
  let loginResult = await attemptLogin(vs);

  // If initial attempt failed due to session drop (not a verified bad password), retry once with fresh ViewState
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
  async function fetchPage(targetUrl: string, refererUrl: string): Promise<{ html: string | null; finalUrl: string; status: number; error?: string }> {
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
    const htmlData: Record<string, string | null> = {
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
    let cspRes: { html: string | null; finalUrl: string; status: number; error?: string } | null = null;
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
    debugLogs.push("Starting atomic autofill_surveys macro on Supabase proxy...");
    const evaluatedCourses: string[] = [];
    const qaBaseUrl = "https://cms.bahria.edu.pk/Sys/Student/QualityAssurance/";
    const qaListUrl = "https://cms.bahria.edu.pk/Sys/Student/QualityAssurance/QualityAssuranceSurveys.aspx";

    let lastReferer = loginResult.finalUrl;
    const MAX_SURVEYS = 20; // Safety bound
    const visitedUrls = new Set<string>();

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
      let pendingSurvey: { name: string; href: string; fullUrl: string } | null = null;

      for (const row of rowMatches) {
        if (row.includes("SurveyStudentCourseWise.aspx") || /<a[^>]+href=["'][^"']*SurveyStudentCourseWise/i.test(row)) {
          const hrefMatch = row.match(/href=["']?([^"'>\s]*SurveyStudentCourseWise\.aspx[^"'>\s]*)["']?/i);
          if (hrefMatch && hrefMatch[1]) {
            const rawHref = hrefMatch[1].replace(/&amp;/g, '&');
            let testUrl = rawHref;
            if (!testUrl.startsWith("http")) {
              try { testUrl = new URL(rawHref, qaBaseUrl).toString(); } catch (_e) { testUrl = qaBaseUrl + rawHref.replace(/^\//, ''); }
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
      const radioGroups = new Map<string, Array<{ value: string; id: string }>>();
      for (const rTag of radioMatches) {
        const nameM = rTag.match(/name=["']([^"']+)["']/i);
        const valM = rTag.match(/value=["']([^"']*)["']/i);
        const idM = rTag.match(/id=["']([^"']+)["']/i);
        if (nameM && nameM[1]) {
          const rName = nameM[1];
          if (!radioGroups.has(rName)) radioGroups.set(rName, []);
          radioGroups.get(rName)!.push({
            value: valM ? valM[1] : "",
            id: idM ? idM[1] : ""
          });
        }
      }

      // Select option index 2 (Uncertain/middle) for each radio group
      for (const [rName, rOptions] of radioGroups.entries()) {
        if (rOptions.length === 0) continue;
        let chosenVal = "";
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

// ── STANDARD NATIVE REDIRECT PROXY ────────────────────────────────────────
async function nativeRedirectProxy(request: Request, initialTargetUrlStr: string) {
  let currentUrl = initialTargetUrlStr;
  let redirectCount = 0;
  const MAX_REDIRECTS = 10;

  const allSetCookies: string[] = [];
  let cookieHeader = request.headers.get("Cookie") || "";
  let isFirstRequest = true;
  let lastReferer = request.headers.get("Referer") || "";

  while (redirectCount <= MAX_REDIRECTS) {

    let fetchReq: Request;
    if (isFirstRequest) {
      const init: RequestInit = { redirect: 'manual', method: request.method };
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        init.body = request.body;
      }
      const headers = new Headers();
      const safeHeaders = ['accept', 'accept-language', 'user-agent', 'content-type'];
      for (const h of safeHeaders) {
        const val = request.headers.get(h);
        if (val) headers.set(h, val);
      }
      if (lastReferer) headers.set("Referer", lastReferer);
      if (cookieHeader) headers.set("Cookie", cookieHeader);
      init.headers = headers;

      fetchReq = new Request(currentUrl, init);
      isFirstRequest = false;
    } else {
      const headers = new Headers();
      const safeHeaders = ['accept', 'accept-language', 'user-agent'];
      for (const h of safeHeaders) {
        const val = request.headers.get(h);
        if (val) headers.set(h, val);
      }
      if (lastReferer) headers.set("Referer", lastReferer);
      if (cookieHeader) headers.set("Cookie", cookieHeader);

      fetchReq = new Request(currentUrl, {
        method: 'GET',
        headers,
        redirect: 'manual'
      });
    }

    const response = await fetchUpstream(fetchReq);

    let hopCookies: string[] = [];
    if (typeof response.headers.getSetCookie === 'function') {
      hopCookies = response.headers.getSetCookie();
    } else {
      for (const [key, value] of response.headers.entries()) {
        if (key.toLowerCase() === 'set-cookie') {
          hopCookies.push(value);
        }
      }
    }

    if (hopCookies.length > 0) {
      allSetCookies.push(...hopCookies);

      const cookieMap = new Map<string, string>();
      if (cookieHeader) {
        cookieHeader.split(';').forEach(pair => {
          const [k, ...vParts] = pair.split('=');
          if (k) cookieMap.set(k.trim(), vParts.join('=').trim());
        });
      }

      hopCookies.forEach(c => {
        const [keyVal] = c.split(';');
        const [k, ...vParts] = keyVal.split('=');
        if (k) cookieMap.set(k.trim(), vParts.join('=').trim());
      });

      cookieHeader = Array.from(cookieMap.entries())
        .map(([k, v]) => `${k}=${v}`)
        .join('; ');
    }

    const status = response.status;
    if (status >= 300 && status < 400) {
      const location = response.headers.get("Location");
      if (!location) {
        return buildFinalResponse(response, allSetCookies, currentUrl);
      }
      const nextUrl = new URL(location, currentUrl).toString();

      lastReferer = currentUrl;
      currentUrl = nextUrl;

      redirectCount++;
    } else {
      return buildFinalResponse(response, allSetCookies, currentUrl);
    }
  }

  return new Response("Max redirects exceeded", {status: 502});
}

function buildFinalResponse(finalResponse: Response, allSetCookies: string[], finalUrl: string) {
  const res = new Response(finalResponse.body, finalResponse);
  res.headers.set("Access-Control-Allow-Origin", "*");

  if (allSetCookies.length > 0) {
    res.headers.set("X-Proxy-Set-Cookie", allSetCookies.join("|||"));
  }

  res.headers.delete("X-Worker-Debug-Log");
  res.headers.set("X-Final-Url", finalUrl);

  return res;
}

// ── MAIN SERVER ───────────────────────────────────────────────────────────
serve(async (request: Request) => {
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

  const supplied = request.headers.get('X-LMS-Proxy-Key') || '';
  if (!await validServiceKey(supplied, Deno.env.get('LMS_PROXY_SERVICE_KEY') || '')) {
    return new Response('Unauthorized', {status: 401});
  }

  const url = new URL(request.url);

  // 1. Check for Macro action
  if (url.searchParams.get("macro") === "true" || request.headers.get("X-Proxy-Macro") === "true") {
    try {
      const payload = await request.json();
      return await handleMacroRequest(payload);
    } catch (err: any) {
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

  // 2. Standard Transparent Proxy
  const targetUrl = url.searchParams.get('url');
  if (!targetUrl) {
    return new Response("Missing 'url' parameter or 'macro=true'", { status: 400 });
  }

  const useNativeRedirects = url.searchParams.get('native_redirects') === 'true';

  try {
    const checkedTarget = checkUpstreamUrl(targetUrl).href;
    if (!useNativeRedirects) {
      const headers = new Headers();
      const safeHeaders = ['accept', 'accept-language', 'user-agent', 'referer', 'content-type', 'cookie'];
      for (const h of safeHeaders) {
        const val = request.headers.get(h);
        if (val) headers.set(h, val);
      }
      const init: RequestInit = {
        method: request.method,
        headers: headers,
        redirect: 'manual'
      };
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        init.body = request.body;
      }
      const newReq = new Request(checkedTarget, init);
      const response = await fetchUpstream(newReq);
      const newRes = new Response(response.body, response);
      newRes.headers.delete("X-Worker-Debug-Log");
      newRes.headers.set("Access-Control-Allow-Origin", "*");
      return newRes;
    } else {
      return await nativeRedirectProxy(request, checkedTarget);
    }
  } catch (err: any) {
    return err instanceof UpstreamDestinationError
      ? new Response(err.message, {status: err.status})
      : new Response("Upstream request failed.", {status: 502});
  }
});
