/**
 * Cloudflare Worker proxy for NeonRoute bug reports -> GitHub Issues.
 * Keeps GITHUB_TOKEN strictly server-side via Wrangler secrets.
 */

const MAX_TITLE_LEN = 200;
const MAX_BODY_LEN = 16000;
const MAX_META_LEN = 64;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
};

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...CORS_HEADERS,
    },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    if (request.method !== "POST") {
      return jsonResponse(
        { ok: false, error: "Method Not Allowed. Use POST." },
        405
      );
    }

    const contentType = request.headers.get("Content-Type") || "";
    if (!contentType.toLowerCase().includes("application/json")) {
      return jsonResponse(
        { ok: false, error: "Content-Type must be application/json." },
        400
      );
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonResponse({ ok: false, error: "Invalid JSON body." }, 400);
    }

    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return jsonResponse(
        { ok: false, error: "JSON payload must be an object." },
        400
      );
    }

    const { title, body, app_version: appVersion, category } = payload;

    if (typeof title !== "string" || typeof body !== "string") {
      return jsonResponse(
        { ok: false, error: "Fields 'title' and 'body' must be strings." },
        400
      );
    }

    const cleanTitle = title.trim();
    const cleanBody = body.trim();

    if (!cleanTitle || !cleanBody) {
      return jsonResponse(
        { ok: false, error: "Fields 'title' and 'body' must be non-empty." },
        400
      );
    }

    if (cleanTitle.length > MAX_TITLE_LEN) {
      return jsonResponse(
        {
          ok: false,
          error: `Field 'title' exceeds maximum length of ${MAX_TITLE_LEN} characters.`,
        },
        413
      );
    }

    if (cleanBody.length > MAX_BODY_LEN) {
      return jsonResponse(
        {
          ok: false,
          error: `Field 'body' exceeds maximum length of ${MAX_BODY_LEN} characters.`,
        },
        413
      );
    }

    if (appVersion !== undefined && typeof appVersion !== "string") {
      return jsonResponse(
        { ok: false, error: "Optional field 'app_version' must be a string." },
        400
      );
    }
    if (category !== undefined && typeof category !== "string") {
      return jsonResponse(
        { ok: false, error: "Optional field 'category' must be a string." },
        400
      );
    }

    const owner = (env.GITHUB_OWNER || "").trim();
    const repo = (env.GITHUB_REPO || "").trim();
    const token = (env.GITHUB_TOKEN || "").trim();

    if (!owner || !repo || !token) {
      return jsonResponse(
        { ok: false, error: "Bug report service is not configured." },
        500
      );
    }

    let issueBody = cleanBody;
    const metaLines = [];
    if (appVersion && appVersion.trim()) {
      metaLines.push(`- **App Version**: ${appVersion.trim().slice(0, MAX_META_LEN)}`);
    }
    if (category && category.trim()) {
      metaLines.push(`- **Category**: ${category.trim().slice(0, MAX_META_LEN)}`);
    }
    if (metaLines.length > 0 && issueBody.length + 100 <= MAX_BODY_LEN) {
      issueBody = `${metaLines.join("\n")}\n\n${issueBody}`;
    }

    const ghUrl = `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues`;

    let ghResponse;
    try {
      ghResponse = await fetch(ghUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "NeonRoute-BugReporter",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: cleanTitle,
          body: issueBody,
        }),
      });
    } catch {
      return jsonResponse(
        { ok: false, error: "Upstream GitHub API request failed." },
        502
      );
    }

    if (!ghResponse.ok) {
      const upstreamStatus = ghResponse.status;
      const mappedStatus = upstreamStatus >= 500 ? 502 : 400;
      return jsonResponse(
        {
          ok: false,
          error: `GitHub API rejected the issue request (HTTP ${upstreamStatus}).`,
        },
        mappedStatus
      );
    }

    let ghData;
    try {
      ghData = await ghResponse.json();
    } catch {
      return jsonResponse(
        { ok: false, error: "Invalid response from GitHub API." },
        502
      );
    }

    return jsonResponse(
      {
        ok: true,
        issue_number: ghData.number,
        issue_url: ghData.html_url,
      },
      201
    );
  },
};
