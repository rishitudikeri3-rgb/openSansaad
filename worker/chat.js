// OpenSansad chat router, deployed as a Cloudflare Worker so the public (static) demo can use
// the model without shipping the API key. Same contract as POST /api/chat in api/app.py:
// the model names which provisions a question is about ({kind, paths}); a second request with
// `facts` (from the page: the provision's history + one verified reason) gets a short casual
// reply, kept only if it passes the same checks as check_reply() in app.py. The prompt and catalogue are read from the
// site's own `api/chat-config` (written by `make site`), so they have one source: app.py.
//
// Secrets: LLM_API_KEY. Vars (wrangler.toml): SITE_URL, ALLOWED_ORIGIN, LLM_BASE_URL,
// MODEL_BULK, MODEL_REASONING.

const KINDS = ["law", "opinion", "other"];
const PER_MINUTE = 20; // per client IP, per instance: two requests per question (best effort; Groq's free tier caps the rest)
const hits = new Map();

function cors(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function reply(env, body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", ...cors(env) },
  });
}

function limited(ip) {
  const now = Date.now(), recent = (hits.get(ip) || []).filter(t => now - t < 60_000);
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 5000) hits.clear();
  return recent.length > PER_MINUTE;
}

async function config(env) {
  const r = await fetch(new URL("api/chat-config", env.SITE_URL), { cf: { cacheTtl: 600 } });
  if (!r.ok) throw new Error(`chat-config: HTTP ${r.status}`);
  return r.json();
}

async function ask(env, model, system, user) {
  const r = await fetch(`${env.LLM_BASE_URL}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.LLM_API_KEY}` },
    body: JSON.stringify({
      model, temperature: 0, response_format: { type: "json_object" },
      messages: [{ role: "system", content: system }, { role: "user", content: user }],
    }),
  });
  if (!r.ok) throw new Error(`${model}: HTTP ${r.status}`);
  const out = JSON.parse((await r.json()).choices[0].message.content);
  if (!out || typeof out !== "object" || Array.isArray(out)) throw new Error(`${model}: not an object`);
  return out;
}

// the model's answer is kept only where it names known provisions; nothing is repaired
function validate(out, catalogue) {
  const kind = KINDS.includes(out.kind) ? out.kind : "other";
  const paths = Array.isArray(out.paths) ? out.paths : [];
  const seen = [...new Set(paths)].filter(p => typeof p === "string" && p in catalogue);
  return { kind, paths: seen.slice(0, 3) };
}

// same checks as check_reply() in api/app.py
function checkReply(reply, facts) {
  const problems = [];
  if (!reply || reply.length > 500) problems.push("empty or too long");
  const years = new Set(facts.match(/\b\d{4}\b/g) || []);
  if ((reply.match(/\b1[89]\d\d\b|\b20\d\d\b/g) || []).some(y => !years.has(y))) problems.push("year not in the facts");
  const who = facts.match(/reason: (The Government|The Law Commission|An MP)/);
  if (who && !reply.toLowerCase().replace(/mps/g, "mp").includes({ "The Government": "government", "The Law Commission": "law commission", "An MP": "mp" }[who[1]]))
    problems.push("does not say who gave the reason");
  if (facts.includes("(their own view") && !/\bown (view|opinion|take)|\bopinion\b|\btheir view\b/i.test(reply))
    problems.push("does not say the MP's reason is their own view");
  if (facts.includes("reason: none found") && !/couldn.?t find|could not find|no (written )?reason|not sure why|don.?t know why/i.test(reply))
    problems.push("does not say no reason was found");
  return problems;
}

async function write(env, question, facts) {
  const { write_system } = await config(env);
  let user = `Question: ${question}\n\nFacts:\n${facts}`;
  for (let i = 0; i < 2; i++) { // one re-ask with the problems listed, never a repair
    let reply;
    try {
      reply = String((await ask(env, env.MODEL_BULK, write_system, user)).reply ?? "").trim();
    } catch (e) {
      console.log(String(e));
      return { reply: null };
    }
    const problems = checkReply(reply, facts);
    if (!problems.length) return { reply };
    user += `\n\nYour reply ${JSON.stringify(reply)} was rejected: ${problems.join("; ")}. Try again.`;
  }
  return { reply: null };
}

async function route(env, question) {
  const { system, catalogue } = await config(env);
  // same text as chat_prompt() in api/app.py
  const user = "Parts of the Act (id | heading):\n"
    + Object.entries(catalogue).map(([p, h]) => `${p} | ${h}`).join("\n")
    + `\n\nQuestion: ${question}`;
  for (const model of [env.MODEL_BULK, env.MODEL_REASONING]) { // the small model sometimes fails JSON mode
    try {
      return { ...validate(await ask(env, model, system, user), catalogue), routed_by: "model" };
    } catch (e) {
      console.log(String(e));
    }
  }
  return { kind: "error", paths: [] };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(env) });
    if (request.method !== "POST") return reply(env, { error: "POST a JSON {question}" }, 405);
    let question, facts;
    try {
      const body = await request.json();
      question = String(body.question ?? "").trim();
      facts = body.facts == null ? null : String(body.facts).slice(0, 1200);
    } catch {
      return reply(env, { error: "invalid JSON" }, 400);
    }
    if (!question || question.length > 300) return reply(env, { error: "question must be 1-300 characters" }, 422);
    if (limited(request.headers.get("CF-Connecting-IP") || "?"))
      return reply(env, { kind: "error", paths: [], error: "too many questions, wait a minute" }, 429);

    // repeated questions are answered from Cloudflare's cache, not the model (free-tier quota;
    // the Cache API is a no-op on *.workers.dev, so this applies once on a custom domain)
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
      question.toLowerCase().replace(/\s+/g, " ") + "\n" + (facts || "")));
    const key = new Request(`https://chat-cache.invalid/${[...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("")}`);
    const hit = await caches.default.match(key);
    if (hit) return reply(env, await hit.json());

    const out = facts ? await write(env, question, facts) : await route(env, question);
    if (out.kind !== "error" && out.reply !== null)
      ctx.waitUntil(caches.default.put(key, new Response(JSON.stringify(out), {
        headers: { "Content-Type": "application/json", "Cache-Control": "max-age=86400" } })));
    return reply(env, out);
  },
};
