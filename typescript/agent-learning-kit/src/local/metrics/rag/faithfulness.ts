/**
 * Faithfulness metric for RAG evaluation.
 * Measures whether the response is faithful to the provided context.
 *
 * @module local/metrics/rag/faithfulness
 */

import { snapshotBoundary } from '../../autoeval/types.js';
import {
    RAGInput,
    RAGConfig,
    RAGResult,
    tokenize,
    jaccardSimilarity,
    normalizeContext,
    extractSentences,
    ngramOverlap
} from './types.js';

/**
 * Calculate faithfulness - whether response is grounded in the context.
 *
 * Extracts claims/statements from the response and checks if they
 * can be supported by the provided context.
 *
 * Faithfulness = (Supported claims) / (Total claims)
 *
 * @param input - RAG evaluation input
 * @param config - Metric configuration
 * @returns Faithfulness score and details
 *
 * @example
 * ```typescript
 * const result = faithfulness({
 *     query: 'What is the capital of France?',
 *     response: 'Paris is the capital of France. It is also known as the City of Light.',
 *     context: ['Paris is the capital of France and its largest city.']
 * });
 * // First claim supported, second partially supported
 * ```
 */
export function faithfulness(
    input: RAGInput,
    config: RAGConfig = {}
): RAGResult {
    try { ({ input, config } = snapshotBoundary({ input, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' }; }
    if (!input || typeof input !== 'object' || Array.isArray(input) ||
        !config || typeof config !== 'object' || Array.isArray(config)) return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    const threshold = config.threshold === undefined ? 0.5 : config.threshold;
    const supportThreshold = config.similarityThreshold === undefined ? 0.25 : config.similarityThreshold;
    const ngramSize = config.ngramSize === undefined ? 2 : config.ngramSize;

    if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1 ||
        typeof supportThreshold !== 'number' || !Number.isFinite(supportThreshold) || supportThreshold <= 0 || supportThreshold > 1 ||
        typeof ngramSize !== 'number' || !Number.isSafeInteger(ngramSize) || ngramSize < 1) {
        return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    }
    if (typeof input.response !== 'string' || (typeof input.context !== 'string' && !Array.isArray(input.context)) ||
        (Array.isArray(input.context) && input.context.some(value => typeof value !== 'string'))) return { score: 0, passed: false, reason: 'NO_EVIDENCE' };
    const contexts = normalizeContext(input.context);
    const combinedContext = contexts.join(' ');

    if (!input.response || input.response.trim().length === 0) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE'
        };
    }

    if (contexts.length === 0 || tokenize(combinedContext).length === 0) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE'
        };
    }

    // Extract claims (sentences) from response
    const claims = extractSentences(input.response).filter(claim => tokenize(claim).length > 0);

    if (claims.length === 0) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE',
            claims: []
        };
    }

    // Check each claim against context
    let supportedCount = 0;
    const claimScores: number[] = [];

    for (const claim of claims) {
        // Calculate support from context using multiple methods
        const tokenSimilarity = jaccardSimilarity(
            tokenize(claim),
            tokenize(combinedContext)
        );

        // Also check n-gram overlap for phrase matching
        const ngramSim = ngramOverlap(claim, combinedContext, ngramSize);

        // Use the higher of the two
        const supportScore = Math.max(tokenSimilarity, ngramSim);
        if (typeof supportScore !== 'number' || !Number.isFinite(supportScore)) return { score: 0, passed: false, reason: 'METRIC_RESULT_INVALID' };
        claimScores.push(supportScore);

        if (supportScore >= supportThreshold) {
            supportedCount++;
        }
    }

    const faithfulnessScore = supportedCount / claims.length;
    const passed = faithfulnessScore >= threshold;

    return {
        score: faithfulnessScore,
        passed,
        reason: passed
            ? `${supportedCount}/${claims.length} claims are supported by context (faithfulness: ${(faithfulnessScore * 100).toFixed(1)}%)`
            : `Only ${supportedCount}/${claims.length} claims are supported (faithfulness: ${(faithfulnessScore * 100).toFixed(1)}%, threshold: ${(threshold * 100).toFixed(1)}%)`,
        claims,
        chunkScores: claimScores
    };
}

/**
 * Alias for faithfulness - groundedness metric
 */
export const groundedness = faithfulness;
