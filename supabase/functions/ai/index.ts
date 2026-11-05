// AI 중계 창구 (docs/ai-relay-design.md §5). 원고(프롬프트·응답)는 저장하지 않는다.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

type Json = Record<string, unknown>;
const MAX_BODY_BYTES = 1_000_000;

const admin = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  { auth: { persistSession: false } },
);

function reply(status: number, code: string, extra: Json = {}): Response {
  return new Response(JSON.stringify({ error: code, ...extra }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function kstDay(): string {
  return new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
}

function nextResetIso(): string {
  const d = new Date(Date.now() + 9 * 3600_000);
  d.setUTCHours(24, 0, 0, 0);
  return new Date(d.getTime() - 9 * 3600_000).toISOString();
}

type Price = { in: number; out: number; cache_write?: number; cache_read?: number };

export function costUsd(
  price: Price | undefined,
  u: { input: number; output: number; cacheWrite: number; cacheRead: number },
): number {
  if (!price) return 0;
  const m = 1_000_000;
  return (
    (u.input * price.in +
      u.output * price.out +
      u.cacheWrite * price.in * (price.cache_write ?? 1.25) +
      u.cacheRead * price.in * (price.cache_read ?? 0.1)) / m
  );
}

async function record(
  userId: string, feature: string, provider: string, model: string,
  usage: { input: number; output: number; cacheWrite: number; cacheRead: number },
  cost: number, status: string, started: number, appVersion: string,
) {
  const { error } = await admin.rpc("ai_record_usage", {
    p_user_id: userId, p_feature: feature, p_provider: provider, p_model: model,
    p_input_tokens: usage.input, p_output_tokens: usage.output,
    p_cache_write_tokens: usage.cacheWrite, p_cache_read_tokens: usage.cacheRead,
    p_cost_usd: cost, p_status: status, p_latency_ms: Date.now() - started,
    p_app_version: appVersion,
  });
  if (error) console.error("record failed", error.message);
}

async function loadState(userId: string) {
  const { data: settings } = await admin.from("ai_settings").select("*").eq("id", 1).maybeSingle();
  const nowIso = new Date().toISOString();
  const { data: access } = await admin
    .from("ai_access").select("*").eq("user_id", userId)
    .lte("starts_at", nowIso)
    .or(`ends_at.is.null,ends_at.gt.${nowIso}`);
  const { data: daily } = await admin
    .from("ai_usage_daily").select("requests,cost_usd")
    .eq("user_id", userId).eq("day_kst", kstDay()).maybeSingle();
  return { settings, access: access ?? [], daily };
}

function limitsOf(settings: Json, access: Json[]) {
  const override = access
    .map((a) => a.daily_usd_override as number | null)
    .filter((v) => v !== null && v !== undefined) as number[];
  const perUserUsd = override.length
    ? Math.max(...override.map(Number))
    : (settings.per_user_daily_usd as number | null);
  return {
    usd: perUserUsd === null || perUserUsd === undefined ? null : Number(perUserUsd),
    requests: settings.per_user_daily_requests as number | null,
    global: settings.global_daily_usd as number | null,
  };
}

Deno.serve(async (req) => {
  const started = Date.now();
  const url = new URL(req.url);
  const route = url.pathname.split("/").filter(Boolean).pop() ?? "";

  // 1. 로그인 확인
  const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!token) return reply(401, "login_required");
  const { data: auth, error: authErr } = await admin.auth.getUser(token);
  if (authErr || !auth?.user) return reply(401, "login_required");
  const userId = auth.user.id;

  const { settings, access, daily } = await loadState(userId);
  // 한도 값이 비어 있으면 창구를 열지 않는다.
  if (!settings || settings.per_user_daily_usd == null || settings.per_user_daily_requests == null ||
      settings.global_daily_usd == null) {
    return reply(503, "feature_disabled");
  }
  const limits = limitsOf(settings, access);
  const usedUsd = Number(daily?.cost_usd ?? 0);
  const usedReq = Number(daily?.requests ?? 0);

  if (route === "quota" && req.method === "GET") {
    return new Response(JSON.stringify({
      has_access: access.length > 0,
      enabled: settings.enabled,
      used_usd: usedUsd, used_requests: usedReq,
      limit_usd: limits.usd, limit_requests: limits.requests,
      remaining_usd: Math.max(0, (limits.usd ?? 0) - usedUsd),
      resets_at: nextResetIso(),
    }), { headers: { "content-type": "application/json; charset=utf-8" } });
  }

  if (req.method !== "POST" || (route !== "gemini" && route !== "claude")) {
    return reply(404, "not_found");
  }
  const provider = route;

  // 크기 상한
  const raw = await req.text();
  if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) return reply(400, "bad_request");
  let body: Json;
  try { body = JSON.parse(raw); } catch { return reply(400, "bad_request"); }
  const feature = String(body.feature ?? "unknown").slice(0, 80);
  const logical = String(body.model ?? "");
  const appVersion = String(body.app_version ?? "").slice(0, 40);
  const request = body.request as Json | undefined;
  if (!request || typeof request !== "object") return reply(400, "bad_request");

  const deny = async (status: number, code: string, logStatus: string) => {
    await record(userId, feature, provider, logical || "-", { input: 0, output: 0, cacheWrite: 0, cacheRead: 0 }, 0, logStatus, started, appVersion);
    return reply(status, code, { resets_at: nextResetIso() });
  };

  // 2. 스위치
  const disabled = (settings.disabled_features as string[]) ?? [];
  if (!settings.enabled || disabled.some((p) => feature.startsWith(p))) {
    return deny(503, "feature_disabled", "denied");
  }
  // 3. 권한
  if (access.length === 0) return deny(403, "no_access", "denied");
  // 4. 한도
  if (usedUsd >= (limits.usd ?? 0) || usedReq >= (limits.requests ?? 0)) {
    return deny(429, "daily_limit", "limited");
  }
  const { data: globalCost } = await admin.rpc("ai_global_cost_today");
  if (Number(globalCost ?? 0) >= Number(limits.global)) return deny(429, "service_limit", "limited");

  // 5. 논리 모델 → 실제 모델, 출력 상한
  const model = (settings.model_map as Record<string, string>)[logical];
  if (!model || !logical.startsWith(provider + ":")) return reply(400, "bad_request");
  const maxOut = Number((settings.max_output_tokens as Record<string, number>)[model] ?? 0);
  const price = (settings.price_map as Record<string, Price>)[model];
  if (!maxOut || !price) return reply(400, "bad_request");

  const empty = { input: 0, output: 0, cacheWrite: 0, cacheRead: 0 };

  // 6. 제공사 호출
  if (provider === "gemini") {
    const gen = ((request.generationConfig ?? {}) as Json);
    gen.maxOutputTokens = Math.min(Number(gen.maxOutputTokens ?? maxOut), maxOut);
    request.generationConfig = gen;
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "x-goog-api-key": Deno.env.get("GEMINI_API_KEY")! },
        body: JSON.stringify(request),
      },
    );
    const text = await res.text();
    if (!res.ok) {
      await record(userId, feature, provider, model, empty, 0, "provider_error", started, appVersion);
      return new Response(JSON.stringify({ error: "provider_error", provider_status: res.status, detail: text.slice(0, 2000) }),
        { status: 502, headers: { "content-type": "application/json; charset=utf-8" } });
    }
    let usage = empty;
    try {
      const meta = JSON.parse(text).usageMetadata ?? {};
      usage = {
        input: Number(meta.promptTokenCount ?? 0),
        output: Number(meta.candidatesTokenCount ?? 0) + Number(meta.thoughtsTokenCount ?? 0),
        cacheWrite: 0, cacheRead: 0,
      };
    } catch { /* 본문 해석 실패는 앱이 처리 */ }
    await record(userId, feature, provider, model, usage, costUsd(price, usage), "ok", started, appVersion);
    return new Response(text, { headers: { "content-type": "application/json; charset=utf-8" } });
  }

  // Claude: 항상 스트리밍으로 받아 앱에 그대로 흘려 보낸다(150초 대기 제한 회피).
  request.model = model;
  request.max_tokens = Math.min(Number(request.max_tokens ?? maxOut), maxOut);
  request.stream = true;
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": Deno.env.get("ANTHROPIC_API_KEY")!,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify(request),
  });
  if (!res.ok || !res.body) {
    const text = await res.text();
    await record(userId, feature, provider, model, empty, 0, "provider_error", started, appVersion);
    const headers: Record<string, string> = { "content-type": "application/json; charset=utf-8" };
    const ra = res.headers.get("retry-after");
    if (ra) headers["retry-after"] = ra;
    return new Response(JSON.stringify({ error: "provider_error", provider_status: res.status, detail: text.slice(0, 2000) }),
      { status: 502, headers });
  }

  // 스트림을 통과시키면서 사용량 이벤트만 엿본다(내용은 저장하지 않음).
  const usage = { ...empty };
  let tail = "";
  const decoder = new TextDecoder();
  const peek = (chunk: string) => {
    tail += chunk;
    const lines = tail.split("\n");
    tail = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      try {
        const ev = JSON.parse(line.slice(5));
        const u = ev.type === "message_start" ? ev.message?.usage : ev.type === "message_delta" ? ev.usage : null;
        if (!u) continue;
        if (u.input_tokens != null) usage.input = Number(u.input_tokens);
        if (u.output_tokens != null) usage.output = Number(u.output_tokens);
        if (u.cache_creation_input_tokens != null) usage.cacheWrite = Number(u.cache_creation_input_tokens);
        if (u.cache_read_input_tokens != null) usage.cacheRead = Number(u.cache_read_input_tokens);
      } catch { /* 무시 */ }
    }
  };
  const stream = res.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      peek(decoder.decode(chunk, { stream: true }));
      controller.enqueue(chunk);
    },
    async flush() {
      await record(userId, feature, provider, model, usage, costUsd(price, usage), "ok", started, appVersion);
    },
  }));
  return new Response(stream, { headers: { "content-type": "text/event-stream" } });
});
