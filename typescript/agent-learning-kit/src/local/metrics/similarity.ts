/**
 * Similarity-based heuristic metrics for local evaluation.
 * Aligned with Python SDK's fi.evals.local.metrics.similarity module.
 */

import { MetricResult, MetricConfig } from './types.js';
import { snapshotBoundary, isIterableCapture } from '../autoeval/types.js';

const incomplete = (reason = 'NO_EVIDENCE'): MetricResult => ({ score: 0, passed: false, reason });
const hasText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function snapshot<T>(value: Iterable<T>): T[] {
    try {
        if (value === null || typeof value !== 'object' || typeof value[Symbol.iterator] !== 'function') throw new Error();
        return Array.from(value);
    } catch { throw new Error('METRIC_CONFIG_INVALID'); }
}

/**
 * Sentence BLEU matching Python ALK's NLTK method1 defaults.
 */
export function bleuScore(
    response: string,
    config: MetricConfig & { reference: string | string[]; ngramWeights?: number[] }
): MetricResult {
    try { config = snapshotBoundary(config, 'METRIC_CONFIG_INVALID'); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return incomplete('METRIC_CONFIG_INVALID');
    if (Array.isArray(config.reference) && isIterableCapture(config.reference)) return incomplete();
    if (typeof response !== 'string') return incomplete();
    const references = Array.isArray(config.reference) ? config.reference : [config.reference];
    if (references.length === 0 || references.some(reference => typeof reference !== 'string')) return incomplete();

    // Python str.split(): includes U+001C..001F and U+0085, excludes U+FEFF.
    // Do not use the shared tokenizer: BLEU preserves case and punctuation.
    const splitTokens = (text: string): string[] => text
        .split(/[\x09-\x0d\x1c-\x20\x85\xa0\u{1680}\u{2000}-\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}]+/u)
        .filter(token => token.length > 0);
    const responseTokens = splitTokens(response);
    const referenceTokens = references.map(splitTokens);
    if (responseTokens.length === 0 || referenceTokens.some(tokens => tokens.length === 0)) return incomplete();

    let weights: number[];
    try { weights = snapshot(config.ngramWeights === undefined ? [0.25, 0.25, 0.25, 0.25] : config.ngramWeights); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (weights.length === 0 || weights.some(weight => !finite(weight) || weight < 0) || !weights.some(weight => weight > 0)) return incomplete('METRIC_CONFIG_INVALID');

    let logSum = 0;
    for (let n = 1; n <= weights.length; n++) {
        const responseNgrams = getNgrams(responseTokens, n);
        const maximumCounts = new Map<string, number>();
        for (const tokens of referenceTokens) {
            const counts = new Map<string, number>();
            for (const ngram of getNgrams(tokens, n)) {
                const key = ngram.join(' ');
                counts.set(key, (counts.get(key) || 0) + 1);
            }
            for (const [key, count] of counts) {
                maximumCounts.set(key, Math.max(maximumCounts.get(key) || 0, count));
            }
        }

        let matches = 0;
        const usedCounts = new Map<string, number>();
        for (const ngram of responseNgrams) {
            const key = ngram.join(' ');
            const refCount = maximumCounts.get(key) || 0;
            const usedCount = usedCounts.get(key) || 0;
            if (usedCount < refCount) {
                matches++;
                usedCounts.set(key, usedCount + 1);
            }
        }

        // NLTK returns zero without unigram overlap, even if its weight is zero.
        if (n === 1 && matches === 0) return { score: 0, passed: false, reason: 'BLEU score: 0.0000' };
        // method1 smooths only zero numerators, including absent n-gram orders.
        const precision = (matches === 0 ? 0.1 : matches) / Math.max(1, responseNgrams.length);
        logSum += weights[n - 1] * Math.log(precision);
    }

    // Closest reference length; equal distances choose the shorter reference.
    const responseLength = responseTokens.length;
    let referenceLength = referenceTokens[0].length;
    for (const tokens of referenceTokens) {
        const distance = Math.abs(tokens.length - responseLength);
        const closestDistance = Math.abs(referenceLength - responseLength);
        if (distance < closestDistance || (distance === closestDistance && tokens.length < referenceLength)) {
            referenceLength = tokens.length;
        }
    }
    const bp = responseLength > referenceLength ? 1 : Math.exp(1 - referenceLength / responseLength);
    // NLTK uses the supplied weights as-is, without scaling or normalization.
    const score = bp * Math.exp(logSum);

    return {
        score,
        passed: score >= 0.5,
        reason: `BLEU score: ${score.toFixed(4)}`
    };
}

/**
 * Calculate ROUGE-L score (Longest Common Subsequence based)
 */
export function rougeScore(
    response: string,
    config: MetricConfig & { reference: string; variant?: 'rouge-l' | 'rouge-1' | 'rouge-2' }
): MetricResult {
    try { config = snapshotBoundary(config, 'METRIC_CONFIG_INVALID'); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return incomplete('METRIC_CONFIG_INVALID');
    if (!hasText(response) || !hasText(config.reference)) return incomplete();
    const variant = config.variant === undefined ? 'rouge-l' : config.variant;
    if (typeof variant !== 'string' || (variant !== 'rouge-l' && variant !== 'rouge-1' && variant !== 'rouge-2')) return incomplete('METRIC_CONFIG_INVALID');
    const responseTokens = tokenize(response);
    const referenceTokens = tokenize(config.reference);

    if (responseTokens.length === 0 || referenceTokens.length === 0) return incomplete();

    let score: number;

    if (variant === 'rouge-l') {
        // LCS-based ROUGE-L
        const lcsLength = longestCommonSubsequence(responseTokens, referenceTokens);
        const precision = lcsLength / responseTokens.length;
        const recall = lcsLength / referenceTokens.length;
        score = precision + recall > 0
            ? (2 * precision * recall) / (precision + recall)
            : 0;
    } else {
        // N-gram based ROUGE
        const n = variant === 'rouge-1' ? 1 : 2;
        const responseNgrams = getNgrams(responseTokens, n);
        const referenceNgrams = getNgrams(referenceTokens, n);

        const responseSet = new Set(responseNgrams.map(ng => ng.join(' ')));
        const referenceSet = new Set(referenceNgrams.map(ng => ng.join(' ')));

        let overlap = 0;
        for (const ng of responseSet) {
            if (referenceSet.has(ng)) overlap++;
        }

        const precision = responseSet.size > 0 ? overlap / responseSet.size : 0;
        const recall = referenceSet.size > 0 ? overlap / referenceSet.size : 0;
        score = precision + recall > 0
            ? (2 * precision * recall) / (precision + recall)
            : 0;
    }

    return {
        score,
        passed: score >= 0.5,
        reason: `${variant.toUpperCase()} score: ${score.toFixed(4)}`
    };
}

/**
 * Calculate recall score (what fraction of reference tokens appear in response)
 */
export function recallScore(
    response: string,
    config: MetricConfig & { reference: string }
): MetricResult {
    try { config = snapshotBoundary(config, 'METRIC_CONFIG_INVALID'); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return incomplete('METRIC_CONFIG_INVALID');
    if (!hasText(response) || !hasText(config.reference)) return incomplete();
    const responseTokens = new Set(tokenize(response));
    const referenceTokens = tokenize(config.reference);
    if (responseTokens.size === 0 || referenceTokens.length === 0) return incomplete();

    let matches = 0;
    for (const token of referenceTokens) {
        if (responseTokens.has(token)) matches++;
    }

    const score = matches / referenceTokens.length;

    return {
        score,
        passed: score >= 0.5,
        reason: `Recall: ${matches}/${referenceTokens.length} tokens (${(score * 100).toFixed(1)}%)`
    };
}

/**
 * Calculate Levenshtein similarity
 */
export function levenshteinSimilarity(
    response: string,
    config: MetricConfig & { reference: string }
): MetricResult {
    try { config = snapshotBoundary(config, 'METRIC_CONFIG_INVALID'); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return incomplete('METRIC_CONFIG_INVALID');
    if (!hasText(response) || !hasText(config.reference)) return incomplete();
    const distance = levenshteinDistance(response, config.reference);
    const maxLen = Math.max(response.length, config.reference.length);
    const score = maxLen > 0 ? 1 - distance / maxLen : 1;

    return {
        score,
        passed: score >= 0.5,
        reason: `Levenshtein similarity: ${(score * 100).toFixed(1)}% (distance: ${distance})`
    };
}

/**
 * Calculate numeric similarity (for numeric responses)
 */
export function numericSimilarity(
    response: string,
    config: MetricConfig & { reference: number; tolerance?: number }
): MetricResult {
    try { config = snapshotBoundary(config, 'METRIC_CONFIG_INVALID'); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return incomplete('METRIC_CONFIG_INVALID');
    const tolerance = config.tolerance === undefined ? 0 : config.tolerance;
    const reference = config.reference;
    if (!finite(reference) || !finite(tolerance) || tolerance < 0) return incomplete('METRIC_CONFIG_INVALID');
    if (!hasText(response)) return incomplete();
    const parsed = Number(response.trim());
    if (!finite(parsed)) return incomplete('NUMERIC_INPUT_INVALID');
    const diff = Math.abs(parsed - reference);
    const isWithinTolerance = diff <= tolerance;

    // Calculate score based on relative difference
    const maxDiff = Math.max(Math.abs(reference), 1);
    const score = Math.max(0, 1 - diff / maxDiff);

    return {
        score: isWithinTolerance ? 1.0 : score,
        passed: isWithinTolerance,
        reason: isWithinTolerance ? 'CHECK_PASSED' : 'CHECK_FAILED'
    };
}

/**
 * Check if response semantically contains items from a list
 * Simple word-based matching (for true semantic matching, use cloud)
 */
export function semanticListContains(
    response: string,
    config: MetricConfig & { items: string[]; threshold?: number }
): MetricResult {
    try { config = snapshotBoundary(config, 'METRIC_CONFIG_INVALID'); }
    catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return incomplete('METRIC_CONFIG_INVALID');
    const threshold = config.threshold === undefined ? 0.5 : config.threshold;
    if (!finite(threshold) || threshold < 0 || threshold > 1) return incomplete('METRIC_CONFIG_INVALID');
    let items: string[];
    try { items = snapshot(config.items); } catch { return incomplete('METRIC_CONFIG_INVALID'); }
    if (items.some(item => typeof item !== 'string')) return incomplete('METRIC_CONFIG_INVALID');
    if (!hasText(response) || items.length === 0 || items.some(item => tokenize(item).length === 0)) return incomplete();
    const responseWords = new Set(tokenize(response));
    if (responseWords.size === 0) return incomplete();
    let matches = 0;
    for (const item of items) {
        if (tokenize(item).some(word => responseWords.has(word))) matches++;
    }
    const score = matches / items.length;
    const passed = score >= threshold;
    return { score, passed, reason: passed ? 'CHECK_PASSED' : 'CHECK_FAILED' };
}

// Helper functions

function tokenize(text: string): string[] {
    return text
        .toLowerCase()
        .replace(/[^\w\s]/g, ' ')
        .split(/\s+/)
        .filter(token => token.length > 0);
}

function getNgrams(tokens: string[], n: number): string[][] {
    const ngrams: string[][] = [];
    for (let i = 0; i <= tokens.length - n; i++) {
        ngrams.push(tokens.slice(i, i + n));
    }
    return ngrams;
}

function longestCommonSubsequence(a: string[], b: string[]): number {
    const m = a.length;
    const n = b.length;
    const dp: number[][] = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));

    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            if (a[i - 1] === b[j - 1]) {
                dp[i][j] = dp[i - 1][j - 1] + 1;
            } else {
                dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
            }
        }
    }

    return dp[m][n];
}

function levenshteinDistance(a: string, b: string): number {
    const m = a.length;
    const n = b.length;
    const dp: number[][] = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));

    for (let i = 0; i <= m; i++) dp[i][0] = i;
    for (let j = 0; j <= n; j++) dp[0][j] = j;

    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            if (a[i - 1] === b[j - 1]) {
                dp[i][j] = dp[i - 1][j - 1];
            } else {
                dp[i][j] = 1 + Math.min(
                    dp[i - 1][j],     // deletion
                    dp[i][j - 1],     // insertion
                    dp[i - 1][j - 1]  // substitution
                );
            }
        }
    }

    return dp[m][n];
}
