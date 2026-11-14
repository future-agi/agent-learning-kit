/**
 * Cloud eval registry — source of truth for eval metadata.
 *
 * Mirrors python/fi/evals/core/cloud_registry.py. Fetches the full template
 * list from `/sdk/api/v1/get-evals/` once per (baseUrl, apiKey, secretKey) tuple and
 * caches the result. Exposes helpers that turn user-supplied kwargs into
 * the exact key set the backend will accept, so the SDK never drifts from
 * the backend when evals are added/renamed.
 */
import axios from "axios";
import { snapshotBoundary } from '../local/autoeval/types.js';
import { createHmac, randomBytes } from 'node:crypto';

// Per-process HMAC prevents cache identities exposing credential material.
const cacheSalt = randomBytes(32);
type RegistryCacheKey = string;
type RegistryEntry = Record<string, any>;

const _CACHE = new Map<RegistryCacheKey, Record<string, RegistryEntry>>();

/**
 * Aliases user kwargs can use → canonical backend keys.
 * Direct key match in user_inputs always wins over an alias lookup.
 */
const KEY_ALIASES: Record<string, readonly string[]> = {
    // Bidirectional output↔input — some evals only accept one (e.g.
    // prompt_injection wants `input`, toxicity wants `output`).
    output: ["output", "response", "answer", "generated", "input"],
    input: ["input", "query", "question", "prompt_input", "output"],
    context: ["context", "contexts"],
    expected: ["expected", "expected_output", "expected_response", "ground_truth"],
    expected_value: ["expected_value", "expected_output", "expected_response", "ground_truth"],
    generated_value: ["generated_value", "output", "response", "answer"],
    reference: ["reference", "expected_output", "expected_response", "ground_truth"],
    hypothesis: ["hypothesis", "output", "response"],
    text: ["text", "output", "content"],
    conversation: ["conversation", "messages"],
    prompt: ["prompt", "instructions", "system_prompt"],
    system_prompt: ["system_prompt", "prompt", "instructions"],
    image: ["image", "image_url", "input_image_url"],
    caption: ["caption", "output"],
    instruction: ["instruction", "prompt"],
    instructions: ["instructions", "prompt"],
    images: ["images", "image_urls", "input_image_urls"],
    input_pdf: ["input_pdf", "pdf"],
    json_content: ["json_content", "json", "expected_output"],
    input_audio: ["input_audio", "audio"],
    audio: ["audio", "input_audio"],
    generated_audio: ["generated_audio", "audio", "output"],
    generated_transcript: ["generated_transcript", "transcript", "output"],
};


function cacheKey(baseUrl: string, apiKey?: string, secretKey?: string): RegistryCacheKey {
    if (typeof baseUrl !== 'string' || !baseUrl.trim() ||
        (apiKey !== undefined && typeof apiKey !== 'string') || (secretKey !== undefined && typeof secretKey !== 'string')) throw new Error('REGISTRY_CONFIG_INVALID');
    return createHmac('sha256', cacheSalt)
        .update(JSON.stringify([baseUrl.replace(/\/$/, ''), apiKey ?? '', secretKey ?? '']))
        .digest('hex');
}

function assertEnabled(): void {
    if (process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY === '1') throw new Error('REGISTRY_DISABLED');
}

function requiredKeys(info: RegistryEntry | undefined): string[] {
    if (!info) throw new Error('TEMPLATE_UNKNOWN');
    try {
        const value = info.config?.required_keys;
        if (value === null || typeof value !== 'object' || typeof value[Symbol.iterator] !== 'function') throw new Error();
        const keys = Array.from(value) as unknown[];
        if (keys.length === 0 || keys.some(k => typeof k !== 'string' || !k.trim())) throw new Error();
        return keys as string[];
    } catch { throw new Error('REGISTRY_INVALID'); }
}

function mapRequired(required: string[], userInputs: Record<string, any>): Record<string, any> {
    userInputs = snapshotBoundary(userInputs, 'REQUIRED_INPUT_MISSING');
    const mapped: Record<string, any> = {};
    if (!userInputs || typeof userInputs !== 'object' || Array.isArray(userInputs)) throw new Error('REQUIRED_INPUT_MISSING');
    for (const key of required) {
        const aliases = Object.prototype.hasOwnProperty.call(KEY_ALIASES, key) ? KEY_ALIASES[key] : [];
        const source = [key, ...aliases].find(alias => Object.prototype.hasOwnProperty.call(userInputs, alias));
        if (source === undefined || userInputs[source] === null || userInputs[source] === undefined) throw new Error('REQUIRED_INPUT_MISSING');
        Object.defineProperty(mapped, key, { value: userInputs[source], enumerable: true, writable: true, configurable: true });
    }
    return mapped;
}

export interface LoadRegistryOptions {
    baseUrl: string;
    apiKey?: string;
    secretKey?: string;
    forceRefresh?: boolean;
}

export async function loadRegistry(
    opts: LoadRegistryOptions
): Promise<Record<string, RegistryEntry>> {
    opts = snapshotBoundary(opts, 'REGISTRY_CONFIG_INVALID');
    if (!opts || typeof opts !== 'object' || Array.isArray(opts)) throw new Error('REGISTRY_CONFIG_INVALID');
    assertEnabled();
    const key = cacheKey(opts.baseUrl, opts.apiKey, opts.secretKey);
    if (opts.forceRefresh !== undefined && typeof opts.forceRefresh !== 'boolean') throw new Error('REGISTRY_CONFIG_INVALID');
    if (opts.forceRefresh !== true && _CACHE.has(key)) {
        return _CACHE.get(key)!;
    }

    const url = `${opts.baseUrl.replace(/\/$/, "")}/sdk/api/v1/get-evals/`;
    const headers: Record<string, string> = {};
    if (opts.apiKey) headers["X-Api-Key"] = opts.apiKey;
    if (opts.secretKey) headers["X-Secret-Key"] = opts.secretKey;

    // A failed refresh invalidates old metadata; never silently reuse it.
    _CACHE.delete(key);
    let resp;
    try {
        resp = await axios.get(url, { headers, timeout: 30000 });
    } catch {
        throw new Error('REGISTRY_UNAVAILABLE');
    }
    // Axios response instances carry transport internals; only data is a
    // registry record. Access failures are sanitized before metadata capture.
    let body: any;
    try { body = snapshotBoundary(resp?.data, 'REGISTRY_INVALID'); }
    catch { throw new Error('REGISTRY_INVALID'); }
    const items = body?.result;
    const status = body?.status;
    if ((status !== undefined && status !== true) || !Array.isArray(items) || items.length === 0) throw new Error('REGISTRY_INVALID');
    const byName: Record<string, RegistryEntry> = Object.create(null);
    for (const item of items) {
        if (!item || typeof item.name !== 'string' || !item.name.trim() || byName[item.name]) throw new Error('REGISTRY_INVALID');
        const keys = requiredKeys(item);
        byName[item.name] = Object.freeze({ ...item, config: Object.freeze({ ...item.config, required_keys: Object.freeze(keys) }) });
    }
    Object.freeze(byName);
    _CACHE.set(key, byName);
    return byName;
}


export async function getTemplateInfo(
    name: string,
    opts: LoadRegistryOptions
): Promise<RegistryEntry | undefined> {
    if (typeof name !== 'string' || !name.trim()) throw new Error('TEMPLATE_UNKNOWN');
    const reg = await loadRegistry(opts);
    if (!Object.prototype.hasOwnProperty.call(reg, name)) throw new Error('TEMPLATE_UNKNOWN');
    return reg[name];
}


export async function getRequiredKeys(
    name: string,
    opts: LoadRegistryOptions
): Promise<string[]> {
    const info = await getTemplateInfo(name, opts);
    return requiredKeys(info);
}


/**
 * Build the exact payload the backend expects for an eval, by:
 *   1. Looking up the eval's required_keys from the cached registry.
 *   2. Taking each required key from userInputs directly if present.
 *   3. Otherwise resolving via known aliases.
 *   4. Dropping any keys the backend doesn't accept (the api is strict).
 *
 * Unknown templates, unavailable metadata and missing aliases reject with
 * fixed reason codes before any evaluation request is submitted.
 */
export async function mapInputsToBackend(
    name: string,
    userInputs: Record<string, any>,
    opts: LoadRegistryOptions
): Promise<Record<string, any>> {
    userInputs = snapshotBoundary(userInputs, 'REQUIRED_INPUT_MISSING');
    opts = snapshotBoundary(opts, 'REGISTRY_CONFIG_INVALID');
    return mapRequired(await getRequiredKeys(name, opts), userInputs);
}


/** Synchronous variant — uses cached data. Throws if not preloaded. */
export function mapInputsToBackendSync(
    name: string,
    userInputs: Record<string, any>,
    baseUrl: string,
    apiKey?: string
): Record<string, any> {
    userInputs = snapshotBoundary(userInputs, 'REQUIRED_INPUT_MISSING');
    assertEnabled();
    if (typeof name !== 'string' || !name.trim()) throw new Error('TEMPLATE_UNKNOWN');
    // The frozen sync signature carries no secret key. It may only reuse a
    // secretless load, never borrow metadata fetched under another identity.
    const cached = _CACHE.get(cacheKey(baseUrl, apiKey));
    if (!cached) throw new Error('REGISTRY_NOT_LOADED');
    return mapRequired(requiredKeys(cached[name]), userInputs);
}


/** Clear cache (exposed for tests). */
export function __clearRegistryCache(): void {
    _CACHE.clear();
}
