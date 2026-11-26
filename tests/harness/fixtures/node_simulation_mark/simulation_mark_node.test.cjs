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
  // The span starts after the call file is written and before the export.
  const start = fs.statSync(callFile, { bigint: true }).mtimeNs + 1000n;
  // The exporter's own values for our keys lose to ours, and each key is sent once.
  const body = JSON.stringify(
    otlp(
      [
        span("llm", start, [
          { key: "fi.span.kind", value: { stringValue: "LLM" } },
          { key: "fi.simulator.call_execution_id", value: { stringValue: "stale-call" } },
        ]),
      ],
      {
        attributes: [
          { key: "service.name", value: { stringValue: "fixed" } },
          { key: "project_name", value: { stringValue: "their project" } },
          { key: "fi.simulation.job_id", value: { stringValue: "stale-job" } },
        ],
      },
    ),
  );
  const script = `
    import { request as esmRequest } from "node:http";
    import { Readable } from "node:stream";
    import zlib from "node:zlib";
    const body = ${JSON.stringify(body)};
    const collector = "http://127.0.0.1:${collector.port}";
    const other = "http://127.0.0.1:${other.port}";
    const auth = { "X-Api-Key": "customer-key", "X-Secret-Key": "customer-secret" };
    // A runtime that posts OTLP JSON with fetch, with a fixed resource.
    await fetch(collector + "/v1/traces", {
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
    const rawResource = exported.resourceSpans[0].resource.attributes.map((one) => one.key);
    const rawSpan = exported.resourceSpans[0].scopeSpans[0].spans[0].attributes.map((one) => one.key);
    assert.equal(new Set(rawResource).size, rawResource.length, rawResource.join());
    assert.equal(new Set(rawSpan).size, rawSpan.length, rawSpan.join());
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

test("one batch with two resources and several scopes is marked in every group", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  fs.writeFileSync(callFile, "call-a");
  const start = fs.statSync(callFile, { bigint: true }).mtimeNs + 1000n;
  const group = (service) => ({
    resource: {
      attributes: [
        { key: "service.name", value: { stringValue: service } },
        { key: "fi.simulation.job_id", value: { stringValue: "stale-job" } },
      ],
    },
    scopeSpans: ["agent", "llm"].map((scope) => ({
      scope: { name: scope },
      spans: [span(`${service}-${scope}-1`, start), span(`${service}-${scope}-2`, start + 10n)],
    })),
  });
  const body = JSON.stringify({ resourceSpans: [group("first"), group("second")] });
  const script = `
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: ${JSON.stringify(body)},
    });
  `;

  const result = await run(script, envFor(collector, callFile));

  assert.equal(result.code, 0, result.stderr);
  const exported = JSON.parse(collector.seen[0].body.toString());
  assert.equal(exported.resourceSpans.length, 2);
  const names = [];
  for (const resourceSpan of exported.resourceSpans) {
    const resource = attributes(resourceSpan.resource.attributes);
    assert.equal(resource["fi.simulation.job_id"], "job-1");
    assert.equal(resource["project_name"], "Our Agent");
    assert.equal(resourceSpan.scopeSpans.length, 2);
    for (const scopeSpan of resourceSpan.scopeSpans) {
      for (const one of scopeSpan.spans) {
        names.push(one.name);
        assert.equal(attributes(one.attributes)["fi.simulator.call_execution_id"], "call-a", one.name);
      }
    }
  }
  assert.equal(names.length, 8);
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
    // A look sees the file after the span starts: a window ends at the last look that saw it.
    await wait(250);
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
    for (const body of ["{not json", "{nor this", "[]"]) {
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
      ["ingest-key", "{nor this"],
      ["ingest-key", "[]"],
    ],
  );
});

test("the Future AGI keys in the environment are never set or changed", async () => {
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
    // The ingest pair stays under its own names: an SDK that reads FI_API_KEY finds none.
    [full, {}],
    [{ ...full, FI_BASE_URL: "https://collector.example" }, { FI_BASE_URL: "https://collector.example" }],
    // The customer's own key is left exactly as it is.
    [{ ...full, FI_API_KEY: "customer-key" }, { FI_API_KEY: "customer-key" }],
    [
      { ...full, FI_API_KEY: "customer-key", FI_SECRET_KEY: "customer-secret" },
      { FI_API_KEY: "customer-key", FI_SECRET_KEY: "customer-secret" },
    ],
  ];
  for (const [env, expected] of cases) {
    const result = await run(script, env);
    assert.equal(result.code, 0, result.stderr);
    assert.match(result.stderr, /simulation mark hook: applied/);
    assert.doesNotMatch(result.stderr, /failed/);
    assert.deepEqual(JSON.parse(result.stdout), expected);
  }
});

test("a span that starts after the call file is removed, before the hook looks, has no call", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  // The event loop is held from the removal to the export, so no poll sees the removal first.
  const script = `
    import fs from "node:fs";
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const hold = (ms) => { const until = Date.now() + ms; while (Date.now() < until); };
    const ns = () => BigInt(Date.now()) * 1000000n;
    const span = (name, start) => ({ traceId: "0af7651916cd43dd8448eb211c80319c", spanId: "b7ad6b7169203331", name,
      startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1000n), attributes: [] });
    fs.writeFileSync(${JSON.stringify(callFile)}, "call-a");
    await wait(250);
    const duringA = ns();
    await wait(250);
    fs.unlinkSync(${JSON.stringify(callFile)});
    hold(50);
    const afterA = ns();
    hold(30);
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ resourceSpans: [{ scopeSpans: [{ spans: [span("a", duringA), span("after", afterA)] }] }] }),
    });
  `;
  const result = await run(script, envFor(collector, callFile));
  assert.equal(result.code, 0, result.stderr);
  const exported = JSON.parse(collector.seen[0].body.toString());
  const stamps = Object.fromEntries(
    exported.resourceSpans[0].scopeSpans[0].spans.map((one) => [
      one.name,
      attributes(one.attributes)["fi.simulator.call_execution_id"] ?? null,
    ]),
  );
  assert.deepEqual(stamps, { a: "call-a", after: null });
});

test("a buffer the caller reuses after its write is acknowledged does not change the export", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const body = JSON.stringify(otlp([span("llm", 1n)]));
  const script = `
    import http from "node:http";
    const body = ${JSON.stringify(body)};
    for (const make of [(text) => Buffer.from(text), (text) => new Uint8Array(Buffer.from(text))]) {
      await new Promise((resolve, reject) => {
        const req = http.request("http://127.0.0.1:${collector.port}/v1/traces", {
          method: "POST", headers: { "content-type": "application/json" },
        }, (res) => { res.resume(); res.on("end", resolve); });
        req.on("error", reject);
        const chunk = make(body);
        req.write(chunk, () => {
          chunk.fill(0x78);
          req.end();
        });
      });
    }
  `;
  const result = await run(script, envFor(collector));
  assert.equal(result.code, 0, result.stderr);
  assert.doesNotMatch(result.stderr, /failed/);
  assert.equal(collector.seen.length, 2);
  for (const sent of collector.seen) {
    const exported = JSON.parse(sent.body.toString());
    assert.equal(exported.resourceSpans[0].scopeSpans[0].spans[0].name, "llm");
    assert.equal(attributes(exported.resourceSpans[0].resource.attributes)["fi.simulation.job_id"], "job-1");
  }
});

test("a request on a socket the caller chose is not given the key, whatever host it names", async (t) => {
  const collector = await stub();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-sock-"));
  const socketPath = path.join(dir, "other.sock");
  const seen = [];
  const other = http.createServer((req, res) => {
    seen.push(req.headers);
    req.resume();
    req.on("end", () => res.end("{}"));
  });
  await new Promise((resolve) => other.listen(socketPath, resolve));
  t.after(() => {
    collector.server.close();
    other.close();
  });
  const script = `
    import http from "node:http";
    await new Promise((resolve, reject) => {
      const req = http.request({
        socketPath: ${JSON.stringify(socketPath)}, host: "127.0.0.1", port: ${collector.port},
        path: "/v1/traces", method: "POST",
        headers: { "content-type": "application/json", "x-api-key": "customer-key" },
      }, (res) => { res.resume(); res.on("end", resolve); });
      req.on("error", reject);
      req.end("{}");
    });
  `;
  const result = await run(script, envFor(collector));
  assert.equal(result.code, 0, result.stderr);
  assert.equal(collector.seen.length, 0);
  assert.equal(seen.length, 1);
  assert.equal(seen[0]["x-api-key"], "customer-key");
  assert.equal(seen[0]["x-secret-key"], undefined);
});

test("a redirect from the collector is not followed with the ingest key", async (t) => {
  const other = await stub();
  const seen = [];
  const collector = http.createServer((req, res) => {
    seen.push(req.url);
    req.resume();
    req.on("end", () => {
      res.writeHead(307, { location: `http://127.0.0.1:${other.port}/v1/traces` });
      res.end();
    });
  });
  await new Promise((resolve) => collector.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    collector.close();
    other.server.close();
  });
  const port = collector.address().port;
  const script = `
    const response = await fetch("http://127.0.0.1:${port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" }, body: "{}",
    });
    console.log(response.status);
  `;
  const result = await run(script, envFor({ port }));
  assert.equal(result.code, 0, result.stderr);
  assert.equal(seen.length, 1);
  assert.equal(other.seen.length, 0);
  // The exporter sees the redirect as the collector's answer.
  assert.equal(result.stdout.trim(), "307");
});

test("a key the caller sets after the request starts never replaces the ingest key", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const script = `
    import http from "node:http";
    const late = [
      (req) => { req.setHeader("X-Api-Key", "customer-key"); req.setHeader("x-secret-key", "customer-secret"); },
      (req) => { req.setHeader("x-api-key", "customer-key"); req.flushHeaders(); },
      (req) => { req.appendHeader("x-api-key", "customer-key"); req.appendHeader("X-Secret-Key", "customer-secret"); },
    ];
    for (const change of late) {
      await new Promise((resolve, reject) => {
        const req = http.request("http://127.0.0.1:${collector.port}/v1/traces", {
          method: "POST", headers: { "content-type": "application/json" },
        }, (res) => { res.resume(); res.on("end", resolve); });
        req.on("error", reject);
        change(req);
        req.end("{}");
      });
    }
  `;
  const result = await run(script, envFor(collector));
  assert.equal(result.code, 0, result.stderr);
  assert.deepEqual(
    collector.seen.map((sent) => [sent.headers["x-api-key"], sent.headers["x-secret-key"]]),
    [
      ["ingest-key", "ingest-secret"],
      ["ingest-key", "ingest-secret"],
      ["ingest-key", "ingest-secret"],
    ],
  );
});

test("a span gets a call only while the hook saw that call's file, never one it did not start in", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  // The call file is written and removed as the scheduler does it; `hold` keeps the hook's poll
  // from running, as a busy event loop does.
  const script = `
    import fs from "node:fs";
    import path from "node:path";
    const file = ${JSON.stringify(callFile)};
    const writeCall = (id) => {
      fs.rmSync(file, { force: true });
      const staged = path.join(path.dirname(file), ".simulation_call_execution_id.tmp");
      fs.writeFileSync(staged, id);
      fs.chmodSync(staged, 0o444);
      fs.renameSync(staged, file);
    };
    const removeCall = () => fs.rmSync(file, { force: true });
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const hold = (ms) => { const until = Date.now() + ms; while (Date.now() < until); };
    const ns = () => BigInt(Date.now()) * 1000000n;
    const spans = [];
    const span = (name) => spans.push({ traceId: "0af7651916cd43dd8448eb211c80319c", spanId: "b7ad6b7169203331",
      name, startTimeUnixNano: String(ns()), endTimeUnixNano: String(ns() + 1000n), attributes: [] });
    // A, seen by the poll; the next call written within one poll of A's removal.
    writeCall("call-a");
    await wait(150);
    span("in-a");
    await wait(250);
    removeCall();
    hold(15);
    span("gap");
    hold(15);
    writeCall("call-b");
    await wait(250);
    removeCall();
    await wait(250);
    // A whole short call between two polls, right after the one before it.
    writeCall("call-c");
    await wait(250);
    removeCall();
    hold(5);
    writeCall("call-d");
    hold(10);
    span("in-short");
    hold(10);
    removeCall();
    await wait(250);
    // The folder is written again after the removal, before the hook looks.
    writeCall("call-e");
    await wait(250);
    removeCall();
    hold(100);
    span("after-e");
    hold(100);
    fs.writeFileSync(path.join(path.dirname(file), "other-entry"), "x");
    await wait(250);
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ resourceSpans: [{ scopeSpans: [{ spans }] }] }),
    });
  `;
  const result = await run(script, envFor(collector, callFile));
  assert.equal(result.code, 0, result.stderr);
  const exported = JSON.parse(collector.seen[0].body.toString());
  const stamps = Object.fromEntries(
    exported.resourceSpans[0].scopeSpans[0].spans.map((one) => [
      one.name,
      attributes(one.attributes)["fi.simulator.call_execution_id"] ?? null,
    ]),
  );
  // A call the hook never saw leaves its span unstamped; it is never given the call before it.
  assert.ok([null, "call-d"].includes(stamps["in-short"]), JSON.stringify(stamps));
  assert.deepEqual({ ...stamps, "in-short": null }, { "in-a": "call-a", gap: null, "in-short": null, "after-e": null });
});

test("a client that copies the request's headers to follow a redirect never copies the ingest key", async (t) => {
  const other = await stub();
  const collector = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      res.writeHead(307, { location: `http://127.0.0.1:${other.port}/v1/traces` });
      res.end();
    });
  });
  await new Promise((resolve) => collector.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    collector.close();
    other.server.close();
  });
  const port = collector.address().port;
  // As superagent does: the next hop is built from req.getHeaders().
  const script = `
    import http from "node:http";
    const seen = await new Promise((resolve, reject) => {
      const req = http.request("http://127.0.0.1:${port}/v1/traces", {
        method: "POST", headers: { "content-type": "application/json", "x-api-key": "customer-key" },
      }, (res) => {
        res.resume();
        const next = http.request(res.headers.location, { method: "POST", headers: req.getHeaders() }, (last) => {
          last.resume();
          last.on("end", () => resolve([req.getHeader("x-api-key"), req.getHeader("x-secret-key")]));
        });
        next.on("error", reject);
        next.end("{}");
      });
      req.on("error", reject);
      req.end("{}");
    });
    console.log(JSON.stringify(seen));
  `;
  const result = await run(script, envFor({ port }));
  assert.equal(result.code, 0, result.stderr);
  assert.equal(other.seen.length, 1);
  assert.equal(other.seen[0].headers["x-api-key"], "customer-key");
  assert.equal(other.seen[0].headers["x-secret-key"], undefined);
  assert.deepEqual(JSON.parse(result.stdout), ["customer-key", null]);
});

test("a request whose agent picks its own connection is not given the key", async (t) => {
  const collector = await stub();
  const other = await stub();
  t.after(() => {
    collector.server.close();
    other.server.close();
  });
  const script = `
    import http from "node:http";
    import net from "node:net";
    const elsewhere = () => net.connect(${other.port}, "127.0.0.1");
    class Elsewhere extends http.Agent {
      createConnection() { return elsewhere(); }
    }
    const patched = new http.Agent();
    patched.createConnection = elsewhere;
    const agents = [
      () => new Elsewhere(),
      () => new http.Agent({ host: "127.0.0.1", port: ${other.port} }),
      () => patched,
      () => { http.globalAgent = new Elsewhere(); return undefined; },
    ];
    for (const make of agents) {
      await new Promise((resolve, reject) => {
        const req = http.request({
          host: "127.0.0.1", port: ${collector.port}, path: "/v1/traces", method: "POST", agent: make(),
          headers: { "content-type": "application/json", "x-api-key": "customer-key" },
        }, (res) => { res.resume(); res.on("end", resolve); });
        req.on("error", reject);
        req.end("{}");
      });
    }
  `;
  const result = await run(script, envFor(collector));
  assert.equal(result.code, 0, result.stderr);
  assert.equal(collector.seen.length, 0);
  assert.deepEqual(
    other.seen.map((sent) => [sent.headers["x-api-key"], sent.headers["x-secret-key"]]),
    Array(4).fill(["customer-key", undefined]),
  );
});

test("a call id the exporter set itself is dropped from a span the hook does not stamp", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  const forged = [
    { key: "fi.simulator.call_execution_id", value: { stringValue: "someone-elses-call" } },
    { key: "fi.span.kind", value: { stringValue: "LLM" } },
  ];
  const body = JSON.stringify(otlp([span("forged", 1n, forged)]));
  const script = `
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" }, body: ${JSON.stringify(body)},
    });
  `;
  // With a call file (no call running at the span's start) and without one.
  for (const env of [envFor(collector, callFile), envFor(collector)]) {
    const result = await run(script, env);
    assert.equal(result.code, 0, result.stderr);
  }
  assert.equal(collector.seen.length, 2);
  for (const sent of collector.seen) {
    const exported = JSON.parse(sent.body.toString());
    assert.deepEqual(attributes(exported.resourceSpans[0].scopeSpans[0].spans[0].attributes), { "fi.span.kind": "LLM" });
  }
});

test("an ingest key that is not a valid header value turns the hook off, with one line", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const body = JSON.stringify(otlp([span("llm", 1n)]));
  const script = `
    import http from "node:http";
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json", "x-api-key": "customer-key" }, body: ${JSON.stringify(body)},
    });
    await new Promise((resolve, reject) => {
      const req = http.request("http://127.0.0.1:${collector.port}/v1/traces", {
        method: "POST", headers: { "content-type": "application/json", "x-api-key": "customer-key" },
      }, (res) => { res.resume(); res.on("end", resolve); });
      req.on("error", reject);
      req.end(${JSON.stringify(body)});
    });
  `;
  const bad = [
    { FI_SIMULATION_INGEST_API_KEY: "ingest\r\nx-injected: 1" },
    { FI_SIMULATION_INGEST_SECRET_KEY: "ingest\tsecret" },
    { FI_SIMULATION_INGEST_API_KEY: "ingest-k\u00e9y" },
  ];
  for (const keys of bad) {
    const result = await run(script, { ...envFor(collector), ...keys });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stderr, "simulation mark hook: failed\n");
  }
  assert.equal(collector.seen.length, 2 * bad.length);
  for (const sent of collector.seen) {
    assert.equal(sent.headers["x-api-key"], "customer-key");
    assert.equal(sent.body.toString(), body);
  }
});

test("a request that resolves the collector's name itself is not given the key", async (t) => {
  // The collector's name resolves nowhere; only a custom lookup reaches the server on its port.
  const other = await stub();
  t.after(() => other.server.close());
  const script = `
    import http from "node:http";
    const lookup = (host, options, callback) => {
      if (typeof options === "function") callback = options;
      if (options && options.all) callback(null, [{ address: "127.0.0.1", family: 4 }]);
      else callback(null, "127.0.0.1", 4);
    };
    const requests = [
      { lookup },
      { agent: new http.Agent({ lookup }) },
      { set: () => { http.globalAgent = new http.Agent({ lookup }); } },
    ];
    for (const { set, ...extra } of requests) {
      if (set) set();
      await new Promise((resolve, reject) => {
        const req = http.request({
          host: "collector.invalid", port: ${other.port}, path: "/v1/traces", method: "POST", ...extra,
          headers: { "content-type": "application/json", "x-api-key": "customer-key" },
        }, (res) => { res.resume(); res.on("end", resolve); });
        req.on("error", reject);
        req.end("{}");
      });
    }
  `;
  const env = {
    ...envFor(other),
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: `http://collector.invalid:${other.port}/v1/traces`,
  };
  const result = await run(script, env);
  assert.equal(result.code, 0, result.stderr);
  assert.deepEqual(
    other.seen.map((sent) => [sent.headers["x-api-key"], sent.headers["x-secret-key"]]),
    Array(3).fill(["customer-key", undefined]),
  );
});

test("a fetch with its own dispatcher is not given the key", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const script = `
    const seen = [];
    // A dispatcher decides where the request goes; this one only records what it was handed.
    const dispatcher = {
      dispatch(options, handler) {
        seen.push(Object.fromEntries(
          Object.entries(options.headers || {}).map(([name, value]) => [name.toLowerCase(), value]),
        ));
        handler.onError(new Error("recorded"));
        return true;
      },
    };
    try {
      await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
        method: "POST", dispatcher, body: "{}",
        headers: { "content-type": "application/json", "x-api-key": "customer-key" },
      });
    } catch (_) {}
    console.log(JSON.stringify(seen.map((headers) => [headers["x-api-key"], headers["x-secret-key"]])));
  `;
  const result = await run(script, envFor(collector));
  assert.equal(result.code, 0, result.stderr);
  // JSON writes the absent secret header as null.
  assert.deepEqual(JSON.parse(result.stdout.trim().split("\n").pop()), [["customer-key", null]]);
  assert.equal(collector.seen.length, 0);
});

test("a call file replaced while the hook reads it never gives one call's span to the next", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  // Call B replaces call A (as the scheduler does: a staged file renamed into place) right after
  // the hook first finds A: after its stat, or after it opens the file, whichever it does first.
  const script = `
    import fs from "node:fs";
    const file = ${JSON.stringify(callFile)};
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const ns = () => BigInt(Date.now()) * 1000000n;
    const span = (name, start) => ({ traceId: "0af7651916cd43dd8448eb211c80319c", spanId: "b7ad6b7169203331", name,
      startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1000n), attributes: [] });
    let armed = false;
    const replaceWithB = () => {
      armed = false;
      fs.writeFileSync(file + ".tmp", "call-b");
      fs.renameSync(file + ".tmp", file);
    };
    for (const name of ["statSync", "openSync"]) {
      const original = fs[name];
      fs[name] = function (target, ...rest) {
        const result = original.call(this, target, ...rest);
        if (armed && String(target) === file) replaceWithB();
        return result;
      };
    }
    fs.writeFileSync(file + ".tmp", "call-a");
    fs.renameSync(file + ".tmp", file);
    const startA = fs.statSync(file, { bigint: true }).mtimeNs;
    armed = true;
    await wait(250);
    const duringB = ns();
    await wait(250);
    const spans = [span("a", startA + 1000n), span("b", duringB)];
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ resourceSpans: [{ scopeSpans: [{ spans }] }] }),
    });
  `;
  const result = await run(script, envFor(collector, callFile));
  assert.equal(result.code, 0, result.stderr);
  const exported = JSON.parse(collector.seen[0].body.toString());
  const calls = Object.fromEntries(
    exported.resourceSpans[0].scopeSpans[0].spans.map((one) => [
      one.name,
      attributes(one.attributes)["fi.simulator.call_execution_id"] ?? null,
    ]),
  );
  // A's span is A's or unstamped, never B's; B's span is B's.
  assert.notEqual(calls.a, "call-b");
  assert.equal(calls.b, "call-b");
});

test("only the configured scheme, host, port and path are the collector", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const base = `127.0.0.1:${collector.port}`;
  const body = JSON.stringify(otlp([span("llm", 1n)]));
  const script = `
    import http from "node:http";
    const body = ${JSON.stringify(body)};
    const headers = { "content-type": "application/json", "x-api-key": "customer-key", "x-secret-key": "customer-secret" };
    const post = (path) => new Promise((resolve, reject) => {
      const req = http.request({ host: "127.0.0.1", port: ${collector.port}, path, method: "POST", headers },
        (res) => { res.resume(); res.on("end", resolve); });
      req.on("error", reject);
      req.end(body);
    });
    for (const path of process.argv.slice(2)) {
      await fetch("http://${base}" + path, { method: "POST", headers, body });
      await post(path);
    }
  `;
  const cases = [
    // The configured route and traceAI's beside it, and the same host's other routes.
    { endpoint: `http://${base}/collector/v1/traces`, paths: ["/collector/v1/traces", "/collector/v1/traces/", "/collector/tracer/v1/traces"], ours: true },
    { endpoint: `http://${base}/collector/v1/traces`, paths: ["/v1/traces", "/tracer/v1/traces", "/other/v1/traces", "/collector/tracer/check"], ours: false },
    // Plain http to the port of an https collector.
    { endpoint: `https://${base}/v1/traces`, paths: ["/v1/traces"], ours: false },
  ];
  for (const { endpoint, paths, ours } of cases) {
    collector.seen.length = 0;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-mark-"));
    const file = path.join(dir, "agent.mjs");
    fs.writeFileSync(file, script);
    const result = await new Promise((resolve) => {
      const child = spawn(process.execPath, [file, ...paths], {
        env: {
          PATH: process.env.PATH,
          NODE_OPTIONS: `--require="${HOOK}"`,
          ...envFor(collector),
          OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: endpoint,
        },
        stdio: ["ignore", "pipe", "pipe"],
      });
      let stderr = "";
      child.stderr.on("data", (chunk) => (stderr += chunk));
      child.on("exit", (code) => resolve({ code, stderr }));
    });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(collector.seen.length, paths.length * 2);
    for (const sent of collector.seen) {
      const key = ours ? "ingest-key" : "customer-key";
      assert.equal(sent.headers["x-api-key"], key, `${endpoint} ${sent.url}`);
      const exported = JSON.parse(sent.body.toString());
      assert.equal(
        attributes(exported.resourceSpans[0].resource.attributes)["fi.simulation.job_id"],
        ours ? "job-1" : undefined,
        `${endpoint} ${sent.url}`,
      );
    }
  }
});

test("an exporter's own call id on a span the hook cannot place is kept only when it names a call the hook saw", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  // The tail of call A: its spans start after the hook's last look, so no window holds them.
  const script = `
    import fs from "node:fs";
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const hold = (ms) => { const until = Date.now() + ms; while (Date.now() < until); };
    const ns = () => BigInt(Date.now()) * 1000000n;
    const span = (name, start, call) => ({ traceId: "0af7651916cd43dd8448eb211c80319c", spanId: "b7ad6b7169203331", name,
      startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1000n),
      attributes: call ? [{ key: "fi.simulator.call_execution_id", value: { stringValue: call } }] : [] });
    fs.writeFileSync(${JSON.stringify(callFile)}, "call-a");
    await wait(250);
    hold(150);
    const tail = ns();
    hold(20);
    fs.unlinkSync(${JSON.stringify(callFile)});
    await fetch("http://127.0.0.1:${collector.port}/v1/traces", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ resourceSpans: [{ scopeSpans: [{ spans: [
        span("labeled", tail, "call-a"), span("unseen", tail, "call-z"), span("bare", tail),
      ] }] }] }),
    });
  `;
  const result = await run(script, envFor(collector, callFile));
  assert.equal(result.code, 0, result.stderr);
  const exported = JSON.parse(collector.seen[0].body.toString());
  const stamps = Object.fromEntries(
    exported.resourceSpans[0].scopeSpans[0].spans.map((one) => [
      one.name,
      attributes(one.attributes)["fi.simulator.call_execution_id"] ?? null,
    ]),
  );
  assert.deepEqual(stamps, { labeled: "call-a", unseen: null, bare: null });
});

test("traceAI's own route on the collector is marked; its other routes are not", async (t) => {
  const collector = await stub();
  t.after(() => collector.server.close());
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "alk-call-"));
  const callFile = path.join(dir, "simulation_call_execution_id");
  fs.writeFileSync(callFile, "call-a");
  const start = fs.statSync(callFile, { bigint: true }).mtimeNs + 1000n;
  const body = JSON.stringify(otlp([span("llm", start, [{ key: "fi.span.kind", value: { stringValue: "LLM" } }])]));
  // traceAI TS's register() posts OTLP JSON to <FI_BASE_URL>/tracer/v1/traces, and the platform
  // sets FI_BASE_URL to the collector.
  const script = `
    const headers = { "content-type": "application/json", "X-Api-Key": "customer-key", "X-Secret-Key": "customer-secret" };
    const body = ${JSON.stringify(body)};
    await fetch("http://127.0.0.1:${collector.port}/tracer/v1/traces", { method: "POST", headers, body });
    await fetch("http://127.0.0.1:${collector.port}/tracer/custom-eval-config/check_exists/", { method: "POST", headers, body });
  `;
  const result = await run(script, envFor(collector, callFile));
  assert.equal(result.code, 0, result.stderr);
  const [traces, other] = collector.seen;
  assert.equal(traces.url, "/tracer/v1/traces");
  assert.equal(traces.headers["x-api-key"], "ingest-key");
  assert.equal(traces.headers["x-secret-key"], "ingest-secret");
  const exported = JSON.parse(traces.body.toString());
  assert.equal(attributes(exported.resourceSpans[0].resource.attributes)["fi.simulation.job_id"], "job-1");
  assert.equal(
    attributes(exported.resourceSpans[0].scopeSpans[0].spans[0].attributes)["fi.simulator.call_execution_id"],
    "call-a",
  );
  assert.equal(other.headers["x-api-key"], "customer-key");
  assert.equal(other.body.toString(), body);
});
