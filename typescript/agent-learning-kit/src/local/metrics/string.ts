/**
 * String-based heuristic metrics for local evaluation.
 * Aligned with Python SDK's fi.evals.local.metrics.string module.
 */

import { MetricResult, MetricConfig } from './types.js';
import { snapshotBoundary } from '../autoeval/types.js';

const invalid = (): MetricResult => ({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
const noEvidence = (): MetricResult => ({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
const hasText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
// Called only after capture: nonarray objects here are SDK-owned plain records.
function validConfig(config: MetricConfig): boolean {
    return !!config && typeof config === 'object' && !Array.isArray(config) &&
        (config.caseSensitive === undefined || typeof config.caseSensitive === 'boolean');
}
function validLength(value: unknown): value is number {
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}
function verdict(passed: boolean, passCode: string, failCode: string, score = passed ? 1 : 0): MetricResult {
    return { score, passed, reason: passed ? passCode : failCode };
}
// A required but empty keyword set is no validation evidence, not vacuous success.
function keywordEvidence(config: MetricConfig): MetricResult | undefined {
    if (!Array.isArray(config.keywords) || config.keywords.some((value: unknown) => typeof value !== 'string')) return invalid();
    if (config.keywords.length === 0 || config.keywords.some((value: string) => !hasText(value))) return noEvidence();
    return undefined;
}

/** Check if text matches a regex pattern. */
export function regex(
    response: string,
    config: MetricConfig & { pattern: string }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config)) return invalid();
    // Preserve the existing missing-pattern exception for a genuine record.
    // Malformed containers and caller accessor failures still return invalid.
    if (config.pattern === undefined || config.pattern === '') throw new Error('Regex pattern is required');
    if (!hasText(config.pattern) || (config.flags !== undefined && typeof config.flags !== 'string')) return invalid();
    if (!hasText(response)) return noEvidence();
    try {
        const pattern = new RegExp(config.pattern, config.flags === undefined ? '' : config.flags);
        return verdict(pattern.test(response), 'REGEX_MATCH', 'REGEX_MISMATCH');
    } catch { return invalid(); }
}

/** Check if text contains a keyword. */
export function contains(
    response: string,
    config: MetricConfig & { keyword: string; caseSensitive?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || typeof config.keyword !== 'string') return invalid();
    if (!hasText(response) || !hasText(config.keyword)) return noEvidence();
    const text = config.caseSensitive ? response : response.toLowerCase();
    const keyword = config.caseSensitive ? config.keyword : config.keyword.toLowerCase();
    return verdict(text.includes(keyword), 'KEYWORD_FOUND', 'KEYWORD_MISSING');
}

/** Check if text contains all specified keywords. */
export function containsAll(
    response: string,
    config: MetricConfig & { keywords: string[]; caseSensitive?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config)) return invalid();
    const failure = keywordEvidence(config);
    if (failure) return failure;
    if (!hasText(response)) return noEvidence();
    const text = config.caseSensitive ? response : response.toLowerCase();
    const keywords = config.keywords.map(k => config.caseSensitive ? k : k.toLowerCase());
    const found = keywords.filter(k => text.includes(k));
    return verdict(found.length === keywords.length, 'ALL_KEYWORDS_FOUND', 'KEYWORDS_MISSING', found.length / keywords.length);
}

/** Check if text contains any of the specified keywords. */
export function containsAny(
    response: string,
    config: MetricConfig & { keywords: string[]; caseSensitive?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config)) return invalid();
    const failure = keywordEvidence(config);
    if (failure) return failure;
    if (!hasText(response)) return noEvidence();
    const text = config.caseSensitive ? response : response.toLowerCase();
    const keywords = config.keywords.map(k => config.caseSensitive ? k : k.toLowerCase());
    return verdict(keywords.some(k => text.includes(k)), 'KEYWORD_FOUND', 'KEYWORDS_MISSING');
}

/** Check if text contains none of the specified keywords. */
export function containsNone(
    response: string,
    config: MetricConfig & { keywords: string[]; caseSensitive?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config)) return invalid();
    const failure = keywordEvidence(config);
    if (failure) return failure;
    if (!hasText(response)) return noEvidence();
    const text = config.caseSensitive ? response : response.toLowerCase();
    const keywords = config.keywords.map(k => config.caseSensitive ? k : k.toLowerCase());
    return verdict(!keywords.some(k => text.includes(k)), 'FORBIDDEN_KEYWORDS_ABSENT', 'FORBIDDEN_KEYWORD_FOUND');
}

/** Check if text is a single line (no newlines). */
export function oneLine(response: string): MetricResult {
    // Primitive-only entry: reject objects without invoking caller methods.
    if (!hasText(response)) return noEvidence();
    return verdict(!response.includes('\n'), 'SINGLE_LINE', 'MULTIPLE_LINES');
}

/** Check if text equals expected value. */
export function equals(
    response: string,
    config: MetricConfig & { expected: string; caseSensitive?: boolean; trim?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || typeof config.expected !== 'string' ||
        (config.trim !== undefined && typeof config.trim !== 'boolean')) return invalid();
    if (!hasText(response) || !hasText(config.expected)) return noEvidence();
    let actual = config.trim !== false ? response.trim() : response;
    let expected = config.trim !== false ? config.expected.trim() : config.expected;
    if (!config.caseSensitive) { actual = actual.toLowerCase(); expected = expected.toLowerCase(); }
    return verdict(actual === expected, 'EQUAL', 'NOT_EQUAL');
}

/** Check if text starts with a prefix. */
export function startsWith(
    response: string,
    config: MetricConfig & { prefix: string; caseSensitive?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || typeof config.prefix !== 'string') return invalid();
    if (!hasText(response) || !hasText(config.prefix)) return noEvidence();
    const text = config.caseSensitive ? response : response.toLowerCase();
    const prefix = config.caseSensitive ? config.prefix : config.prefix.toLowerCase();
    return verdict(text.startsWith(prefix), 'PREFIX_MATCH', 'PREFIX_MISMATCH');
}

/** Check if text ends with a suffix. */
export function endsWith(
    response: string,
    config: MetricConfig & { suffix: string; caseSensitive?: boolean }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || typeof config.suffix !== 'string') return invalid();
    if (!hasText(response) || !hasText(config.suffix)) return noEvidence();
    const text = config.caseSensitive ? response : response.toLowerCase();
    const suffix = config.caseSensitive ? config.suffix : config.suffix.toLowerCase();
    return verdict(text.endsWith(suffix), 'SUFFIX_MATCH', 'SUFFIX_MISMATCH');
}

/** Check if text length is less than a threshold. */
export function lengthLessThan(
    response: string,
    config: MetricConfig & { maxLength: number }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || !validLength(config.maxLength)) return invalid();
    if (!hasText(response)) return noEvidence();
    return verdict(response.length < config.maxLength, 'LENGTH_VALID', 'LENGTH_INVALID');
}

/** Check if text length is greater than a threshold. */
export function lengthGreaterThan(
    response: string,
    config: MetricConfig & { minLength: number }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || !validLength(config.minLength)) return invalid();
    if (!hasText(response)) return noEvidence();
    return verdict(response.length > config.minLength, 'LENGTH_VALID', 'LENGTH_INVALID');
}

/** Check if text length is between min and max. */
export function lengthBetween(
    response: string,
    config: MetricConfig & { minLength: number; maxLength: number }
): MetricResult {
    try { ({ response, config } = snapshotBoundary({ response, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return invalid(); }
    if (!validConfig(config) || !validLength(config.minLength) || !validLength(config.maxLength) ||
        config.minLength > config.maxLength) return invalid();
    if (!hasText(response)) return noEvidence();
    return verdict(response.length >= config.minLength && response.length <= config.maxLength, 'LENGTH_VALID', 'LENGTH_INVALID');
}
