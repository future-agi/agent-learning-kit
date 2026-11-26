"use strict";
/*
 * Startup hook that marks a Node process's own OTLP trace exports to our collector.
 *
 * The Node counterpart of `simulation_mark_bootstrap.py`, with the same contract. The sandbox
 * copies this file into the world directory and names it in NODE_OPTIONS (`--require`), so it
 * runs inside the CUSTOMER's Node process before their code. It uses Node's own modules only.
 *
 * Node has no SDK constructor every exporter goes through, so this hook works on the request
 * instead: it wraps `fetch` and `http.request`/`https.request`, and acts only on a POST whose
 * host and port are the collector's (`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) and whose path ends
 * in `/v1/traces`. On such a request it
 *   - replaces only the Future AGI authentication headers with the agent's ingest key, and
 *   - when the body is OTLP JSON (gzip or not): merges our `OTEL_RESOURCE_ATTRIBUTES` pairs
 *     (`project_name` and `fi.simulation.*`) over every resource, and stamps
 *     `fi.simulator.call_execution_id` on every span that started while the sandbox's call
 *     file named that call.
 * A protobuf body is sent as it is, with our key: it is not decoded, so its spans carry no call.
 *
 * The call stamp follows the Python hook, which stamps a span when it starts. Spans here are
 * read when they are exported, which a batching exporter does after the call may have ended or
 * the next one begun, so the hook keeps the recent windows in which it saw the call file (see
 * `CallWindows`) and stamps a span with the call whose window holds its start time. A span that
 * started outside every window is left unstamped, never given another call.
 *
 * Every other request is untouched. Nothing runs without the ingest key and the collector
 * endpoint, or while the sandbox measures the agent itself (`FI_SIMULATION_INSTRUMENT`): that
 * endpoint is then the customer's own. As in the Python hook, the ingest key stays under its own
 * names: the process's FI_* variables are never read, set or changed, so an SDK that reads
 * FI_API_KEY finds only the customer's own key, or none. A failure of this hook never fails the
 * request: the body goes out as the caller wrote it, except that a request to our collector never
 * carries the customer's key, and one fixed line goes to stderr.
 */

const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const nodeModule = require("node:module");
const nodeUrl = require("node:url");
const zlib = require("node:zlib");

const CALL_FILE = "FI_SIMULATION_CALL_FILE";
const CALL_KEY = "fi.simulator.call_execution_id";
const INGEST_API_KEY = "FI_SIMULATION_INGEST_API_KEY";
const INGEST_SECRET_KEY = "FI_SIMULATION_INGEST_SECRET_KEY";
const TRACES_ENDPOINT = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT";
const MEASURE = "FI_SIMULATION_INSTRUMENT";
const AUTH_HEADERS = ["x-api-key", "x-secret-key"];
// An ingest key is printable ASCII.
const HEADER_VALUE = /^[\x20-\x7e]+$/;
const DEFAULT_PORTS = { "http:": 80, "https:": 443 };
const MARK_PREFIX = "fi.simulation.";
const ORIGIN_KEY = "fi.simulation.origin";
const PROJECT_KEY = "project_name";
const TRACES_PATH = "/v1/traces";
const PATCHED = Symbol.for("alk.simulation_mark");
// The call file is looked at this often, and on every export, so a window's edges are known to
// within this many milliseconds.
const POLL_MS = 100;
// A process runs one call at a time; this many recent windows outlast any export queue.
const WINDOWS_KEPT = 64;

let failedWritten = false;

function write(outcome) {
  try {
    if (outcome === "failed") {
      if (failedWritten) return;
      failedWritten = true;
    }
    process.stderr.write(`simulation mark hook: ${outcome}\n`);
  } catch (_) {
    // Nothing to report to.
  }
}

function unquote(value) {
  try {
    return decodeURIComponent(value);
  } catch (_) {
    return value;
  }
}

/** Our pairs of OTEL_RESOURCE_ATTRIBUTES, read as the Python hook reads them; none without the origin mark. */
function readPairs(raw) {
  if (!raw) return null;
  const pairs = new Map();
  for (const item of raw.split(",")) {
    const at = item.indexOf("=");
    if (at < 0) continue;
    const key = item.slice(0, at).trim();
    if (key === PROJECT_KEY || key.startsWith(MARK_PREFIX)) {
      pairs.set(key, unquote(item.slice(at + 1).trim()));
    }
  }
  return pairs.has(ORIGIN_KEY) ? pairs : null;
}

/** The host and port an endpoint names, as `_host_port` in the Python hook gives them. */
function hostPort(endpoint) {
  if (!endpoint) return null;
  let url;
  try {
    url = new URL(endpoint.includes("://") ? endpoint : `unknown://${endpoint}`);
  } catch (_) {
    return null;
  }
  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (!host) return null;
  const port = url.port ? Number(url.port) : DEFAULT_PORTS[url.protocol] ?? null;
  return `${host}:${port}`;
}

/**
 * The windows in which the hook saw the call file name each recent call. A window runs from the
 * file's write time (never before the last look that saw another state) to the last look that
 * saw the file. Between two looks the file may have changed any number of times, so a span that
 * started there is left unstamped rather than given a call it may not have started in.
 */
class CallWindows {
  constructor(path) {
    this.path = path;
    this.seen = null;
    // Just after the latest look: a file seen for the first time was put in place after it.
    this.changedAfter = 0n;
    this.windows = [];
  }

  observe() {
    const before = BigInt(Date.now()) * 1000000n;
    // One open file: its identity and its call are read from the same descriptor, so a call file
    // replaced between the two can never pair one call's identity with the next call's id.
    let fd = null;
    try {
      fd = fs.openSync(this.path, "r");
    } catch (_) {
      // Removed: the current call has ended.
    }
    try {
      this.look(fd, before);
    } finally {
      if (fd !== null) fs.closeSync(fd);
    }
  }

  look(fd, before) {
    let stat = null;
    if (fd !== null) {
      try {
        stat = fs.fstatSync(fd, { bigint: true });
      } catch (_) {
        return; // Look again on the next poll.
      }
    }
    const after = BigInt(Date.now() + 1) * 1000000n;
    const seen = stat === null ? null : `${stat.ino}:${stat.mtimeNs}`;
    const open = this.windows[this.windows.length - 1];
    const previousLook = this.changedAfter;
    if (seen === this.seen) {
      if (seen !== null && open && open.open) open.end = before;
      this.changedAfter = after;
      return;
    }
    let call = null;
    if (stat !== null) {
      try {
        call = fs.readFileSync(fd, "utf8").trim() || null;
      } catch (_) {
        return; // Read again on the next look.
      }
    }
    this.seen = seen;
    this.changedAfter = after;
    if (open) open.open = false;
    if (call === null) return;
    // The file was put in place after the previous look, which saw another state.
    const start = stat.mtimeNs > previousLook ? stat.mtimeNs : previousLook;
    this.windows.push({ call, start, end: before, open: true });
    if (this.windows.length > WINDOWS_KEPT) this.windows.shift();
  }

  at(startNs) {
    for (let i = this.windows.length - 1; i >= 0; i--) {
      const window = this.windows[i];
      if (window.start <= startNs && startNs <= window.end) return window.call;
    }
    return null;
  }
}

function stringAttribute(key, value) {
  return { key, value: { stringValue: value } };
}

/** Replace or add attributes by key, keeping the caller's others. */
function putAttributes(holder, entries) {
  const attributes = Array.isArray(holder.attributes) ? holder.attributes : [];
  const keys = new Set(entries.map(([key]) => key));
  holder.attributes = [
    ...attributes.filter((one) => !(one && keys.has(one.key))),
    ...entries.map(([key, value]) => stringAttribute(key, value)),
  ];
}

function nanos(value) {
  if (typeof value === "string" && /^\d+$/.test(value)) return BigInt(value);
  if (typeof value === "number" && Number.isFinite(value)) return BigInt(Math.trunc(value));
  return null;
}

/** OTLP JSON uses lowerCamelCase; the collector also reads the proto names. */
function field(object, camel, snake) {
  if (!object || typeof object !== "object") return [];
  const value = object[camel] ?? object[snake];
  return Array.isArray(value) ? value : [];
}

/** The body with our marks, or null to send it as it is. */
function markedJson(text, pairs, calls) {
  const request = JSON.parse(text);
  const resourceSpans = field(request, "resourceSpans", "resource_spans");
  if (!resourceSpans.length) return null;
  if (calls) calls.observe();
  for (const resourceSpan of resourceSpans) {
    if (!resourceSpan || typeof resourceSpan !== "object") continue;
    if (pairs) {
      if (!resourceSpan.resource || typeof resourceSpan.resource !== "object") {
        resourceSpan.resource = {};
      }
      putAttributes(resourceSpan.resource, [...pairs]);
    }
    for (const scopeSpan of field(resourceSpan, "scopeSpans", "scope_spans")) {
      for (const span of field(scopeSpan, "spans", "spans")) {
        if (!span || typeof span !== "object") continue;
        const start = nanos(span.startTimeUnixNano ?? span.start_time_unix_nano);
        const call = start === null || !calls ? null : calls.at(start);
        if (call) {
          putAttributes(span, [[CALL_KEY, call]]);
        } else if (Array.isArray(span.attributes)) {
          // Only the hook names a span's call: one the exporter set itself is never sent.
          span.attributes = span.attributes.filter((one) => !(one && one.key === CALL_KEY));
        }
      }
    }
  }
  return JSON.stringify(request);
}

function markedBody(body, contentType, contentEncoding, pairs, calls) {
  if (!/json/i.test(String(contentType || ""))) return null;
  const encoding = String(contentEncoding || "").trim().toLowerCase();
  if (encoding && encoding !== "identity" && encoding !== "gzip") return null;
  const raw = encoding === "gzip" ? zlib.gunzipSync(body) : body;
  const marked = markedJson(raw.toString("utf8"), pairs, calls);
  if (marked === null) return null;
  const out = Buffer.from(marked, "utf8");
  return encoding === "gzip" ? zlib.gzipSync(out) : out;
}

function install() {
  const env = process.env;
  if (env[MEASURE]) return;
  const apiKey = env[INGEST_API_KEY];
  const secretKey = env[INGEST_SECRET_KEY];
  const collector = hostPort(env[TRACES_ENDPOINT]);
  if (!apiKey || !secretKey || collector === null) return;
  if (!HEADER_VALUE.test(apiKey) || !HEADER_VALUE.test(secretKey)) {
    // Not a key: as a header it would fail the request or add one.
    write("failed");
    return;
  }
  const ours = [
    [AUTH_HEADERS[0], apiKey],
    [AUTH_HEADERS[1], secretKey],
  ];
  const pairs = readPairs(env.OTEL_RESOURCE_ATTRIBUTES);
  const calls = env[CALL_FILE] ? new CallWindows(env[CALL_FILE]) : null;
  if (calls) {
    calls.observe();
    setInterval(() => {
      try {
        calls.observe();
      } catch (_) {
        write("failed");
      }
    }, POLL_MS).unref();
  }

  function aimedAtUs(method, protocol, host, port, path) {
    if (String(method || "GET").toUpperCase() !== "POST") return false;
    const pathname = String(path || "/").split("?")[0].replace(/\/+$/, "");
    if (!pathname.endsWith(TRACES_PATH)) return false;
    const resolved = port ? Number(port) : DEFAULT_PORTS[protocol] ?? null;
    return `${String(host || "").toLowerCase().replace(/^\[|\]$/g, "")}:${resolved}` === collector;
  }

  function mark(body, contentType, contentEncoding) {
    try {
      return markedBody(body, contentType, contentEncoding, pairs, calls);
    } catch (_) {
      write("failed");
      return null;
    }
  }

  // --- fetch -------------------------------------------------------------------------------

  const originalFetch = globalThis.fetch;
  if (typeof originalFetch === "function" && !originalFetch[PATCHED]) {
    const wrapped = async function fetch(input, init) {
      let request;
      try {
        const url = new URL(
          typeof input === "string" || input instanceof URL ? String(input) : input.url,
        );
        const method = (init && init.method) || (input && input.method) || "GET";
        // A dispatcher of the caller's own sends the request wherever it chooses: never ours.
        const ownDispatcher = (init && init.dispatcher !== undefined) || (input && input.dispatcher !== undefined);
        if (ownDispatcher || !aimedAtUs(method, url.protocol, url.hostname, url.port, url.pathname)) {
          return originalFetch.call(this, input, init);
        }
        request = new Request(input, init);
      } catch (_) {
        write("failed");
        return originalFetch.call(this, input, init);
      }
      const headers = new Headers(request.headers);
      for (const name of AUTH_HEADERS) headers.delete(name);
      let body;
      try {
        for (const [name, value] of ours) headers.set(name, value);
        body = Buffer.from(await request.arrayBuffer());
      } catch (_) {
        // The body could not be read again: the request goes out as written, without the
        // customer's key, so our collector refuses it instead of storing it in their project.
        write("failed");
        for (const name of AUTH_HEADERS) headers.set(name, "");
        return originalFetch.call(this, new Request(input, { ...init, headers, redirect: "manual" }));
      }
      const marked = mark(body, headers.get("content-type"), headers.get("content-encoding"));
      if (marked !== null) {
        body = marked;
        headers.delete("content-length");
      }
      // A redirect is answered to the exporter, never followed: fetch would carry our key to
      // wherever it points.
      return originalFetch.call(this, new Request(request, { headers, body, redirect: "manual" }));
    };
    wrapped[PATCHED] = true;
    globalThis.fetch = wrapped;
  }

  // --- http and https ----------------------------------------------------------------------

  function headerEntries(headers) {
    if (!headers) return [];
    if (Array.isArray(headers)) {
      const entries = [];
      for (let i = 0; i + 1 < headers.length; i += 2) entries.push([headers[i], headers[i + 1]]);
      return entries;
    }
    return Object.entries(headers);
  }

  function withOurKey(headers) {
    const kept = headerEntries(headers).filter(
      ([name]) => !AUTH_HEADERS.includes(String(name).toLowerCase()),
    );
    return Object.fromEntries([...kept, ...ours]);
  }

  function withoutAuth(headers) {
    const kept = headerEntries(headers).filter(
      ([name]) => !AUTH_HEADERS.includes(String(name).toLowerCase()),
    );
    return Object.fromEntries([...kept, ...AUTH_HEADERS.map((name) => [name, ""])]);
  }

  /** A copy of the chunk: its write is acknowledged at once, after which the caller may reuse it. */
  function chunkBuffer(chunk, encoding) {
    if (chunk === undefined || chunk === null) return null;
    if (chunk instanceof Uint8Array) return Buffer.from(chunk);
    return Buffer.from(String(chunk), typeof encoding === "string" ? encoding : "utf8");
  }

  /**
   * Hold the body until `end`, then send it marked. The auth headers on the wire stay ours, and
   * the request reads back the caller's own values for them: a client that copies the request's
   * headers, to follow a redirect for one, never copies the ingest key.
   */
  function buffered(req, callerHeaders) {
    const originalWrite = req.write;
    const originalEnd = req.end;
    const originalSetHeader = req.setHeader;
    const originalGetHeaders = req.getHeaders;
    const chunks = [];
    const ourValue = new Map(ours);
    const callerValue = new Map(
      headerEntries(callerHeaders)
        .map(([name, value]) => [String(name).toLowerCase(), value])
        .filter(([name]) => ourValue.has(name)),
    );
    req.setHeader = function (name, value) {
      const lower = String(name).toLowerCase();
      if (!ourValue.has(lower)) return originalSetHeader.call(this, name, value);
      callerValue.set(lower, value);
      return originalSetHeader.call(this, name, ourValue.get(lower));
    };
    if (typeof req.appendHeader === "function") {
      const originalAppendHeader = req.appendHeader;
      req.appendHeader = function (name, value) {
        const lower = String(name).toLowerCase();
        if (!ourValue.has(lower)) return originalAppendHeader.call(this, name, value);
        callerValue.set(lower, value);
        return originalSetHeader.call(this, name, ourValue.get(lower));
      };
    }
    const originalRemoveHeader = req.removeHeader;
    req.removeHeader = function (name) {
      const lower = String(name).toLowerCase();
      if (!ourValue.has(lower)) return originalRemoveHeader.call(this, name);
      callerValue.delete(lower);
    };
    const originalGetHeader = req.getHeader;
    req.getHeader = function (name) {
      const lower = String(name).toLowerCase();
      return ourValue.has(lower) ? callerValue.get(lower) : originalGetHeader.call(this, name);
    };
    const originalHasHeader = req.hasHeader;
    req.hasHeader = function (name) {
      const lower = String(name).toLowerCase();
      return ourValue.has(lower) ? callerValue.has(lower) : originalHasHeader.call(this, name);
    };
    req.getHeaders = function () {
      const headers = originalGetHeaders.call(this);
      for (const name of ourValue.keys()) {
        delete headers[name];
        if (callerValue.has(name)) headers[name] = callerValue.get(name);
      }
      return headers;
    };
    req.write = function (chunk, encoding, callback) {
      const done = typeof encoding === "function" ? encoding : callback;
      const buffer = chunkBuffer(chunk, encoding);
      if (buffer) chunks.push(buffer);
      if (typeof done === "function") process.nextTick(done);
      return true;
    };
    req.end = function (chunk, encoding, callback) {
      let done = callback;
      if (typeof chunk === "function") {
        done = chunk;
        chunk = undefined;
      } else if (typeof encoding === "function") {
        done = encoding;
      }
      const last = chunkBuffer(chunk, encoding);
      if (last) chunks.push(last);
      let body = Buffer.concat(chunks);
      if (!req.headersSent) {
        const marked = mark(body, req.getHeader("content-type"), req.getHeader("content-encoding"));
        if (marked !== null) {
          body = marked;
          if (req.getHeader("content-length") !== undefined) {
            req.setHeader("content-length", body.length);
          }
        }
      }
      req.write = originalWrite;
      req.end = originalEnd;
      if (body.length) originalWrite.call(req, body);
      return originalEnd.call(req, done);
    };
  }

  /**
   * Whether the agent connects where the request says: Node's own Agent, with no destination of
   * its own and its own createConnection. Any other agent (a proxy agent, a subclass, one built
   * with a host, a port or its own DNS lookup) may lead anywhere, so its request is never given
   * the key.
   */
  function stockAgent(agent) {
    if (agent === false || agent === undefined || agent === null) return true;
    if (agent.constructor !== http.Agent && agent.constructor !== https.Agent) return false;
    if (Object.prototype.hasOwnProperty.call(agent, "createConnection")) return false;
    const options = agent.options || {};
    return !["host", "hostname", "port", "socketPath", "lookup"].some(
      (name) => options[name] !== undefined,
    );
  }

  function patchRequest(module, defaultProtocol) {
    const original = module.request;
    if (typeof original !== "function" || original[PATCHED]) return;
    const wrapped = function request(...args) {
      let call = args;
      let ourRequest = false;
      let callerHeaders;
      try {
        let url = null;
        let index = 0;
        if (typeof args[0] === "string" || args[0] instanceof URL) {
          url = new URL(String(args[0]));
          index = 1;
        }
        const options = args[index] && typeof args[index] === "object" ? args[index] : null;
        const opts = options || {};
        // Where Node sends it: the options over the URL's, the host and port as ClientRequest
        // reads them. A socket or a DNS lookup the caller or its agent supplies goes wherever it
        // leads, so it is never ours.
        const target = url ? { ...nodeUrl.urlToHttpOptions(url), ...opts } : opts;
        const host = target.hostname || target.host || "localhost";
        const port =
          target.port || target.defaultPort || (target.agent && target.agent.defaultPort) || null;
        if (
          !target.socketPath &&
          typeof target.createConnection !== "function" &&
          target.lookup === undefined &&
          stockAgent(target.agent === undefined ? module.globalAgent : target.agent) &&
          aimedAtUs(target.method, target.protocol || defaultProtocol, host, port, target.path)
        ) {
          ourRequest = true;
          const headers = opts.headers;
          callerHeaders = headers;
          let swapped;
          try {
            swapped = withOurKey(headers);
          } catch (_) {
            write("failed");
            swapped = withoutAuth(headers);
          }
          const replaced = { ...opts, headers: swapped };
          call = options ? [...args] : [...args.slice(0, index), replaced, ...args.slice(index)];
          if (options) call[index] = replaced;
        }
      } catch (_) {
        write("failed");
        call = args;
        ourRequest = false;
      }
      const req = original.apply(this, call);
      if (ourRequest) {
        try {
          buffered(req, callerHeaders);
        } catch (_) {
          write("failed");
        }
      }
      return req;
    };
    wrapped[PATCHED] = true;
    module.request = wrapped;
  }

  patchRequest(http, "http:");
  patchRequest(https, "https:");
  // `import("http")` and `import { request } from "node:http"` read the ESM view of the module.
  nodeModule.syncBuiltinESMExports();
  write("applied");
}

try {
  install();
} catch (_) {
  write("failed");
}
