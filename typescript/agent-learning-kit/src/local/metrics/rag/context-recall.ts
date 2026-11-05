/**
 * Context Recall metric for RAG evaluation.
 * Measures what proportion of relevant information was retrieved.
 *
 * @module local/metrics/rag/context-recall
 */

import { snapshotBoundary } from '../../autoeval/types.js';
import {
    RAGInput,
    RAGConfig,
    RAGResult,
    tokenize,
    jaccardSimilarity,
    normalizeContext,
    extractSentences
} from './types.js';

/**
 * Calculate context recall - proportion of relevant information that was retrieved.
 *
 * Recall = (Number of ground truth contexts found) / (Total ground truth contexts)
 *
 * If no ground truth contexts provided, uses expected_response as reference.
 *
 * @param input - RAG evaluation input
 * @param config - Metric configuration
 * @returns Recall score and details
 *
 * @example
 * ```typescript
 * const result = contextRecall({
 *     query: 'What is machine learning?',
 *     response: 'Machine learning is...',
 *     context: ['ML is a type of AI...'],
 *     ground_truth_contexts: ['ML is a type of AI...', 'ML uses data to learn...']
 * });
 * console.log(result.score); // 0.5 (found 1 of 2 ground truth)
 * ```
 */
export function contextRecall(
    input: RAGInput,
    config: RAGConfig = {}
): RAGResult {
    try { ({ input, config } = snapshotBoundary({ input, config }, 'METRIC_CONFIG_INVALID')); }
    catch { return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' }; }
    if (!input || typeof input !== 'object' || Array.isArray(input) ||
        !config || typeof config !== 'object' || Array.isArray(config)) return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    const threshold = config.threshold === undefined ? 0.5 : config.threshold;
    const similarityThreshold = config.similarityThreshold === undefined ? 0.3 : config.similarityThreshold;
    if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1 ||
        typeof similarityThreshold !== 'number' || !Number.isFinite(similarityThreshold) || similarityThreshold <= 0 || similarityThreshold > 1) {
        return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    }

    if ((typeof input.context !== 'string' && !Array.isArray(input.context)) ||
        (Array.isArray(input.context) && input.context.some(value => typeof value !== 'string')) ||
        (input.expected_response !== undefined && typeof input.expected_response !== 'string') ||
        (input.ground_truth_contexts !== undefined && !Array.isArray(input.ground_truth_contexts))) return { score: 0, passed: false, reason: 'NO_EVIDENCE', recall: 0 };
    const contexts = normalizeContext(input.context).filter(c => tokenize(c).length > 0);
    if (contexts.length === 0) return { score: 0, passed: false, reason: 'NO_EVIDENCE', recall: 0 };
    const combinedContext = contexts.join(' ');
    const contextTokens = tokenize(combinedContext);

    // Determine what to measure recall against
    let references: string[];

    if (input.ground_truth_contexts && input.ground_truth_contexts.length > 0) {
        references = input.ground_truth_contexts;
    } else if (input.expected_response) {
        // Use sentences from expected response as reference
        references = extractSentences(input.expected_response);
    } else {
        return {
            score: 0,
            passed: false,
            reason: 'NO_EVIDENCE',
            recall: 0
        };
    }

    if (references.length === 0 || references.some(reference => typeof reference !== 'string' || tokenize(reference).length === 0)) {
        return { score: 0, passed: false, reason: 'NO_EVIDENCE', recall: 0 };
    }

    // Check which references are covered by retrieved contexts
    let coveredCount = 0;
    const coverageScores: number[] = [];

    for (const reference of references) {
        const refTokens = tokenize(reference);

        // Check against each context chunk
        let maxSimilarity = 0;
        for (const context of contexts) {
            const similarity = jaccardSimilarity(refTokens, tokenize(context));
            maxSimilarity = Math.max(maxSimilarity, similarity);
        }

        coverageScores.push(maxSimilarity);
        if (maxSimilarity >= similarityThreshold) {
            coveredCount++;
        }
    }

    const recall = coveredCount / references.length;
    const passed = recall >= threshold;

    return {
        score: recall,
        passed,
        reason: passed
            ? `Retrieved ${coveredCount}/${references.length} relevant references (recall: ${(recall * 100).toFixed(1)}%)`
            : `Only retrieved ${coveredCount}/${references.length} relevant references (recall: ${(recall * 100).toFixed(1)}%, threshold: ${(threshold * 100).toFixed(1)}%)`,
        recall,
        chunkScores: coverageScores
    };
}
