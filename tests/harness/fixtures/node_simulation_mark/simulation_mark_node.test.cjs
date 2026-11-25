"use strict";
// The Node mark hook, run as the sandbox loads it (NODE_OPTIONS --require) in a real Node
// process, against local stub servers. Run with `node --test`; test_simulation_mark_node.py
// runs it under pytest.

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const zlib = require("node:zlib");

const HOOK = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "..",
  "src",
  "fi",
  "alk",
  "harness",
  "simulation_mark_node.cjs",
);
const MARK =
  "service.name=customer,project_name=Our%20Agent,fi.simulation.origin=simulation," +
  "fi.simulation.job_id=job-1,fi.simulation.run_id=run-1";

function stub() {
  const seen = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks) });
      res.end("{}");
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, seen, port: server.address().port }));
  });
}

function run(script, env) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-mark-"));
  const file = path.join(dir, "agent.mjs");
  fs.writeFileSync(file, script);
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [file], {
      env: { PATH: process.env.PATH, NODE_OPTIONS: `--require="${HOOK}"`, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stderr = "";
    let stdout = "";
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.on("exit", (code) => resolve({ code, stderr, stdout }));
  });
}

function otlp(spans, resource = { attributes: [{ key: "service.name", value: { stringValue: "fixed" } }] }) {
  return {
    resourceSpans: [{ resource, scopeSpans: [{ scope: { name: "agent" }, spans }] }],
  };
}

function span(name, startNs, attributes = []) {
  return {
    traceId: "0af7651916cd43dd8448eb211c80319c",
    spanId: "b7ad6b7169203331",
    name,
    startTimeUnixNano: String(startNs),
    endTimeUnixNano: String(startNs + 1000n),
    attributes,
  };
}

function attributes(list) {
  return Object.fromEntries((list || []).map((one) => [one.key, one.value.stringValue]));
}

function envFor(collector, callFile) {
  return {
    FI_SIMULATION_INGEST_API_KEY: "ingest-key",
    FI_SIMULATION_INGEST_SECRET_KEY: "ingest-secret",
    FI_API_KEY: "customer-key",
    FI_SECRET_KEY: "customer-secret",
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: `http://127.0.0.1:${collector.port}/v1/traces`,
    OTEL_RESOURCE_ATTRIBUTES: MARK,
    FI_SIMULATION_EXPORT: "our_keys",
    ...(callFile ? { FI_SIMULATION_CALL_FILE: callFile } : {}),
  };
}

test("marks fetch and http exports to the collector and leaves every other request alone", async (t) => {
  const collector = await stub();
  const other = await stub();
  t.after(() => {
    collector.server.close();
    other.server.close();
  });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  fs.writeFileSync(callFile, "call-a");
  const start = BigInt(Date.now() + 1000) * 1000000n;
  const body = JSON.stringify(
    otlp([span("llm", start, [{ key: "fi.span.kind", value: { stringValue: "LLM" } }])]),
  );
  const script = `
    import { request as esmRequest } from "node:http";
    import { Readable } from "node:stream";
    import zlib from "node:zlib";
    const body = ${JSON.stringify(body)};
    const collector = "http://127.0.0.1:${collector.port}";
    const other = "http://127.0.0.1:${other.port}";
    const auth = { "X-Api-Key": "customer-key", "X-Secret-Key": "customer-secret" };
    // A runtime that posts OTLP JSON with fetch, to traceAI's path, with a fixed resource.
    await fetch(collector + "/tracer/v1/traces", {
      method: "POST",
      headers: { "content-type": "application/json", ...auth },
      body,
    });
    // The OpenTelemetry JS exporter: http.request from import("http"), the body piped, gzip.
    const { request } = await import("http");
    await new Promise((resolve, reject) => {
      const req = request(new URL(collector + "/v1/traces"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...auth },
      }, (res) => { res.resume(); res.on("end", resolve); });
      req.on("error", reject);
      req.setHeader("Content-Encoding", "gzip");
      const data = new Readable();
      data.push(Buffer.from(body));
      data.push(null);
      data.pipe(zlib.createGzip()).pipe(req);
    });
    // A named ESM import with a Content-Length the hook must correct.
    await new Promise((resolve, reject) => {
      const req = esmRequest({
        host: "127.0.0.1", port: ${collector.port}, path: "/v1/traces", method: "POST",
        headers: { "content-type": "application/json", "content-length": Buffer.byteLength(body), ...auth },
      }, (res) => { res.resume(); res.on("end", resolve); });
      req.on("error", reject);
      req.end(body);
    });
    // Protobuf: our key, the body as written.
    await fetch(collector + "/v1/traces", {
      method: "POST", headers: { "content-type": "application/x-protobuf", ...auth }, body: "\\x0a\\x00",
    });
    // Not an export to the collector: another host, another path, a GET.
    await fetch(other + "/v1/traces", { method: "POST", headers: { "content-type": "application/json", ...auth }, body });
    await fetch(collector + "/tracer/check", { method: "POST", headers: { "content-type": "application/json", ...auth }, body });
    await fetch(collector + "/v1/traces", { headers: auth });
    console.log(JSON.stringify(Object.keys(process.env).filter((name) => /^FI_(API|SECRET)_KEY$/.test(name)).map((name) => process.env[name])));
  `;
  const result = await run(script, envFor(collector, callFile));
  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stderr, /simulation mark hook: applied/);
  assert.doesNotMatch(result.stderr, /failed/);
  // The key is never put in the environment: the customer's stays.
  assert.deepEqual(JSON.parse(result.stdout), ["customer-key", "customer-secret"]);

  const [viaFetch, viaHttp, viaEsm, protobuf, ...rest] = collector.seen;
  for (const sent of [viaFetch, viaHttp, viaEsm]) {
    assert.equal(sent.headers["x-api-key"], "ingest-key");
    assert.equal(sent.headers["x-secret-key"], "ingest-secret");
    const raw = sent.headers["content-encoding"] === "gzip" ? zlib.gunzipSync(sent.body) : sent.body;
    if (sent.headers["content-length"] !== undefined) {
      assert.equal(Number(sent.headers["content-length"]), sent.body.length);
    }
    const exported = JSON.parse(raw.toString());
    const resource = attributes(exported.resourceSpans[0].resource.attributes);
    assert.deepEqual(resource, {
      "service.name": "fixed",
      project_name: "Our Agent",
      "fi.simulation.origin": "simulation",
      "fi.simulation.job_id": "job-1",
      "fi.simulation.run_id": "run-1",
    });
    const spanAttributes = attributes(exported.resourceSpans[0].scopeSpans[0].spans[0].attributes);
    assert.deepEqual(spanAttributes, {
      "fi.span.kind": "LLM",
      "fi.simulator.call_execution_id": "call-a",
    });
  }
  assert.equal(viaHttp.headers["content-encoding"], "gzip");
  assert.equal(protobuf.headers["x-api-key"], "ingest-key");
  assert.equal(protobuf.body.toString("latin1"), "\x0a\x00");

  // Every other request reached its server exactly as written.
  const [checkPath, get] = rest;
  for (const untouched of [other.seen[0], checkPath]) {
    assert.equal(untouched.headers["x-api-key"], "customer-key");
    assert.equal(untouched.body.toString(), body);
  }
  assert.equal(get.method, "GET");
  assert.equal(get.headers["x-api-key"], "customer-key");
});

test("a span is stamped with the call that ran when it started, or with none", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  // The sandbox writes call A, removes it, then writes call B; the spans of A are exported
  // only once B runs, as a batching exporter does.
  const script = `
    import fs from "node:fs";
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const ns = () => BigInt(Date.now()) * 1000000n;
    const span = (name, start) => ({ traceId: "0af7651916cd43dd8448eb211c80319c", spanId: "b7ad6b7169203331", name,
      startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1000n), attributes: [] });
    const before = ns();
    await wait(30);
    fs.writeFileSync(${JSON.stringify(callFile)}, "call-a");
    await wait(250);
    const duringA = ns();
    await wait(30);
    fs.unlinkSync(${JSON.stringify(callFile)});
    await wait(250);
    const between = ns();
    await wait(30);
    fs.writeFileSync(${JSON.stringify(callFile)}, "call-b");
    await wait(250);
    const duringB = ns();
    const spans = [span("before", before), span("a", duringA), span("between", between), span("b", duringB)];
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ resourceSpans: [{ scopeSpans: [{ spans }] }] }),
    });
  `;
  const result = await run(script, envFor(collector));
  // The call file is named by the environment of the run; it does not exist at start.
  assert.equal(result.code, 0, result.stderr);
  const exported = JSON.parse(collector.seen[0].body.toString());
  const stamps = Object.fromEntries(
    exported.resourceSpans[0].scopeSpans[0].spans.map((one) => [
      one.name,
      attributes(one.attributes)["fi.simulator.call_execution_id"] ?? null,
    ]),
  );
  // envFor() without a call file never stamps; this run names it below.
  assert.deepEqual(stamps, { before: null, a: null, between: null, b: null });

  const named = await stub();
  t.after(() => named.server.close());
  fs.rmSync(callFile, { force: true });
  const again = await run(
    script.replaceAll(String(collector.port), String(named.port)),
    envFor(named, callFile),
  );
  assert.equal(again.code, 0, again.stderr);
  const marked = JSON.parse(named.seen[0].body.toString());
  const calls = Object.fromEntries(
    marked.resourceSpans[0].scopeSpans[0].spans.map((one) => [
      one.name,
      attributes(one.attributes)["fi.simulator.call_execution_id"] ?? null,
    ]),
  );
  assert.deepEqual(calls, { before: null, a: "call-a", between: null, b: "call-b" });
  // A resource the exporter left out is given our marks.
  assert.equal(attributes(marked.resourceSpans[0].resource.attributes)["fi.simulation.job_id"], "job-1");
});

test("nothing is changed without the key, without the endpoint, or while the sandbox measures", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const body = JSON.stringify(otlp([span("llm", 1n)]));
  const script = `
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json", "x-api-key": "customer-key" },
      body: ${JSON.stringify(body)},
    });
  `;
  const full = envFor(collector);
  const variants = [
    { ...full, FI_SIMULATION_INGEST_SECRET_KEY: "" },
    { ...full, OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: "" },
    { ...full, FI_SIMULATION_INSTRUMENT: "traceai" },
  ];
  for (const env of variants) {
    const result = await run(script, env);
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stderr, "");
  }
  assert.equal(collector.seen.length, variants.length);
  for (const sent of collector.seen) {
    assert.equal(sent.headers["x-api-key"], "customer-key");
    assert.equal(sent.body.toString(), body);
  }

  // Without the origin mark the resource is not merged; the key and the call stamp still are.
  const result = await run(script, { ...full, OTEL_RESOURCE_ATTRIBUTES: "fi.simulation.job_id=job-1" });
  assert.equal(result.code, 0, result.stderr);
  const sent = collector.seen[variants.length];
  assert.equal(sent.headers["x-api-key"], "ingest-key");
  assert.deepEqual(attributes(JSON.parse(sent.body.toString()).resourceSpans[0].resource.attributes), {
    "service.name": "fixed",
  });
});

test("a body the hook cannot read is sent as written, with our key, and fails once", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const script = `
    for (const body of ["{not json", "[]"]) {
      await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
        method: "POST", headers: { "content-type": "application/json", "x-api-key": "customer-key" }, body,
      });
    }
  `;
  const result = await run(script, envFor(collector));
  assert.equal(result.code, 0, result.stderr);
  assert.equal(result.stderr.match(/simulation mark hook: failed/g).length, 1);
  assert.deepEqual(
    collector.seen.map((sent) => [sent.headers["x-api-key"], sent.body.toString()]),
    [
      ["ingest-key", "{not json"],
      ["ingest-key", "[]"],
    ],
  );
});

test("a process without its own key is given the ingest key and the collector as its base", async () => {
  const script = `
    const { FI_API_KEY, FI_SECRET_KEY, FI_BASE_URL } = process.env;
    console.log(JSON.stringify({ FI_API_KEY, FI_SECRET_KEY, FI_BASE_URL }));
  `;
  const full = {
    ...envFor({ port: 4318 }),
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: "https://collector.example/prefix/v1/traces/",
  };
  delete full.FI_API_KEY;
  delete full.FI_SECRET_KEY;
  const cases = [
    [full, { FI_API_KEY: "ingest-key", FI_SECRET_KEY: "ingest-secret", FI_BASE_URL: "https://collector.example/prefix" }],
    // The platform's own base for traceAI stays.
    [
      { ...full, FI_BASE_URL: "https://collector.example" },
      { FI_API_KEY: "ingest-key", FI_SECRET_KEY: "ingest-secret", FI_BASE_URL: "https://collector.example" },
    ],
    // Either half of the customer's own key leaves the environment as it is.
    [{ ...full, FI_API_KEY: "customer-key" }, { FI_API_KEY: "customer-key" }],
    // Measuring: the hook does nothing at all.
    [{ ...full, FI_SIMULATION_INSTRUMENT: "traceai" }, {}],
  ];
  for (const [env, expected] of cases) {
    const result = await run(script, env);
    assert.equal(result.code, 0, result.stderr);
    assert.doesNotMatch(result.stderr, /failed/);
    assert.deepEqual(JSON.parse(result.stdout), expected);
  }
});
