/**
 * Context Relevance metric for RAG evaluation.
 * Measures how relevant the retrieved context is to the query.
 *
 * @module local/metrics/rag/context-relevance
 */

import { snapshotBoundary } from '../../autoeval/types.js';
import {
    RAGInput,
    RAGConfig,
    RAGResult,
    tokenize,
    jaccardSimilarity,
    normalizeContext,
    ngramOverlap
} from './types.js';

/**
 * Calculate context relevance - how well the retrieved context matches the query.
 *
 * Different from precision in that it measures relevance quality, not just
 * whether contexts match ground truth.
 *
 * @param input - RAG evaluation input
 * @param config - Metric configuration
 * @returns Relevance score and details
 *
 * @example
 * ```typescript
 * const result = contextRelevance({
 *     query: 'What is machine learning?',
 *     response: 'Machine learning is...',
 *     context: [
 *         'Machine learning is a branch of AI that enables computers to learn.',
 *         'Recipe for chocolate cake...'  // Irrelevant
 *     ]
 * });
 * // Score based on how relevant each context chunk is to the query
 * ```
 */
export function contextRelevance(
    input: RAGInput,
    config: RAGConfig = {}
): RAGResult {
    try { ({ input, config } = snapshotBoundary({ input, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' }; }
    if (!input || typeof input !== 'object' || Array.isArray(input) ||
        !config || typeof config !== 'object' || Array.isArray(config)) return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    const threshold = config.threshold === undefined ? 0.5 : config.threshold;
    if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
        return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    }
    const ngramSize = config.ngramSize === undefined ? 2 : config.ngramSize;
    if (typeof ngramSize !== 'number' || !Number.isSafeInteger(ngramSize) || ngramSize < 1) return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };

    if ((typeof input.context !== 'string' && !Array.isArray(input.context)) ||
        (Array.isArray(input.context) && input.context.some(value => typeof value !== 'string'))) return { score: 0, passed: false, reason: 'NO_EVIDENCE' };
    // Preserve source slots and the original denominator. Empty chunks score
    // zero; their positions are never reassigned to a different source chunk.
    const contexts = normalizeContext(input.context);
    const queryTokens = tokenize(typeof input.query === 'string' ? input.query : '');

    if (contexts.length === 0 || contexts.every(value => tokenize(value).length === 0)) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE'
        };
    }

    if (queryTokens.length === 0) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE'
        };
    }

    // Calculate relevance for each context chunk
    const chunkScores: number[] = [];
    const relevantIndices: number[] = [];

    for (let i = 0; i < contexts.length; i++) {
        const contextTokens = tokenize(contexts[i]);
        if (contextTokens.length === 0) { chunkScores.push(0); continue; }

        // Multiple relevance signals
        const tokenSim = jaccardSimilarity(queryTokens, contextTokens);
        const ngramSim = ngramOverlap(input.query, contexts[i], ngramSize);

        // Check for query key terms
        const keyTerms = queryTokens.filter(t => t.length > 3);
        let keyTermsFound = 0;
        for (const term of keyTerms) {
            if (contextTokens.some(ct => ct.includes(term) || term.includes(ct))) {
                keyTermsFound++;
            }
        }
        const keyTermScore = keyTerms.length > 0 ? keyTermsFound / keyTerms.length : 0.5;

        // Weighted combination
        const relevanceScore = (tokenSim * 0.3) + (ngramSim * 0.3) + (keyTermScore * 0.4);
        chunkScores.push(relevanceScore);

        if (relevanceScore >= threshold * 0.5) {
            relevantIndices.push(i);
        }
    }

    // Overall score is average of chunk scores
    const avgScore = chunkScores.reduce((a, b) => a + b, 0) / chunkScores.length;
    const passed = avgScore >= threshold;

    return {
        score: avgScore,
        passed,
        reason: passed
            ? `Context is relevant to query (avg score: ${(avgScore * 100).toFixed(1)}%)`
            : `Context may not be sufficiently relevant (avg score: ${(avgScore * 100).toFixed(1)}%, threshold: ${(threshold * 100).toFixed(1)}%)`,
        chunkScores,
        relevantIndices
    };
}

/**
 * Calculate context utilization - how much of the context is used in the response.
 *
 * @param input - RAG evaluation input
 * @param config - Metric configuration
 * @returns Utilization score and details
 */
export function contextUtilization(
    input: RAGInput,
    config: RAGConfig = {}
): RAGResult {
    try { ({ input, config } = snapshotBoundary({ input, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' }; }
    if (!input || typeof input !== 'object' || Array.isArray(input) ||
        !config || typeof config !== 'object' || Array.isArray(config)) return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    const threshold = config.threshold === undefined ? 0.5 : config.threshold;
    if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
        return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    }

    if ((typeof input.context !== 'string' && !Array.isArray(input.context)) ||
        (Array.isArray(input.context) && input.context.some(value => typeof value !== 'string'))) return { score: 0, passed: false, reason: 'NO_EVIDENCE' };
    // Preserve source slots and the original denominator. Empty chunks score
    // zero; their positions are never reassigned to a different source chunk.
    const contexts = normalizeContext(input.context);

    if (contexts.length === 0 || contexts.every(value => tokenize(value).length === 0)) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE'
        };
    }

    if (typeof input.response !== 'string' || input.response.trim().length === 0) {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE'
        };
    }

    const responseTokens = tokenize(input.response);
    if (responseTokens.length === 0) return { score: 0, passed: false, reason: 'NO_EVIDENCE' };

    // Check how much of each context is reflected in response
    const utilizationScores: number[] = [];
    const usedIndices: number[] = [];

    for (let i = 0; i < contexts.length; i++) {
        const contextTokens = tokenize(contexts[i]);

        // Count tokens from context that appear in response
        let usedTokens = 0;
        for (const token of contextTokens) {
            if (responseTokens.includes(token)) {
                usedTokens++;
            }
        }

        const utilization = contextTokens.length > 0 ? usedTokens / contextTokens.length : 0;
        utilizationScores.push(utilization);

        if (utilization > 0.1) {
            usedIndices.push(i);
        }
    }

    // Overall utilization: proportion of contexts that contributed
    const contextContribution = usedIndices.length / contexts.length;

    // Average utilization across used contexts
    const avgUtilization = utilizationScores.length > 0
        ? utilizationScores.reduce((a, b) => a + b, 0) / utilizationScores.length
        : 0;

    // Combined score
    const score = (contextContribution * 0.5) + (avgUtilization * 0.5);
    const passed = score >= threshold;

    return {
        score,
        passed,
        reason: passed
            ? `${usedIndices.length}/${contexts.length} contexts contributed to response (utilization: ${(score * 100).toFixed(1)}%)`
            : `Low context utilization: ${usedIndices.length}/${contexts.length} contexts used (score: ${(score * 100).toFixed(1)}%, threshold: ${(threshold * 100).toFixed(1)}%)`,
        chunkScores: utilizationScores,
        relevantIndices: usedIndices
    };
}
