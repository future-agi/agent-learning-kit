/**
 * Type definitions for AutoEval pipeline.
 * @module local/autoeval/types
 */

import { MetricResult } from '../metrics/types.js';

// Shared internal C5 boundary. Never trust Object.isFrozen on caller objects:
// only records produced here may bypass capture. Own non-enumerable fields are
// included, so a supplied false verdict cannot disappear during a spread.
const boundaryRecords = new WeakSet<object>();
// Identity checks never inspect a rejected value's prototype or properties.
const boundaryErrors = new WeakSet<object>();
class BoundaryError extends Error {
    constructor(code: string) {
        super(code);
        boundaryErrors.add(this);
    }
}
export function snapshotBoundary<T>(value: T, code: string, allowCallbacks = false): T {
    const active = new Set<object>();
    const copies = new Map<object, unknown>();
    const capture = (source: any, depth: number, reason: string): any => {
        try {
            if (source === null || source === undefined || typeof source === 'string' ||
                typeof source === 'boolean' || typeof source === 'number') return source;
            // Only scanner callbacks are opaque capabilities; their return values
            // cross a separate data boundary. They are never interpreted as data.
            if (allowCallbacks && typeof source === 'function') return source;
            if (typeof source !== 'object' || depth > 64 || active.has(source)) throw new BoundaryError(reason);
            if (boundaryRecords.has(source)) return source;
            if (copies.has(source)) return copies.get(source);
            active.add(source);
            let owned: any;
            if (Array.isArray(source)) {
                // Array slots, not a caller-supplied iterator, define the evidence.
                // Read length once, including for array proxies, without coercion.
                const length = source.length;
                if (typeof length !== 'number' || !Number.isSafeInteger(length) ||
                    length < 0 || length > 0xffffffff) throw new BoundaryError(reason);
                owned = new Array(length);
                for (let index = 0; index < length; index++) {
                    // Densify holes to undefined so downstream every/map/some
                    // cannot skip missing evidence or use an inherited slot.
                    const member = Object.prototype.hasOwnProperty.call(source, index) ? source[index] : undefined;
                    owned[index] = capture(member, depth + 1, reason);
                }
            } else {
                const iterator = source[Symbol.iterator];
                if (iterator !== undefined) {
                    if (typeof iterator !== 'function') throw new BoundaryError(reason);
                    // Deliberately supported non-array iterables keep their contract.
                    // Read their iterator property once and capture each yield once.
                    owned = Array.from({ [Symbol.iterator]: () => iterator.call(source) },
                        item => capture(item, depth + 1, reason));
                } else {
                    const proto = Object.getPrototypeOf(source);
                    if (proto !== Object.prototype && proto !== null) throw new BoundaryError(reason);
                    owned = Object.create(null);
                    for (const key of Object.getOwnPropertyNames(source)) {
                        const fieldReason = key === 'customPatterns' ? 'INVALID_SECURITY_PATTERN' : reason;
                        let member;
                        try { member = source[key]; } catch { throw new BoundaryError(fieldReason); }
                        owned[key] = capture(member, depth + 1, fieldReason);
                    }
                }
            }
            active.delete(source);
            Object.freeze(owned);
            copies.set(source, owned);
            boundaryRecords.add(owned);
            return owned;
        } catch (error) {
            if (typeof error === 'object' && error !== null && boundaryErrors.has(error)) throw error;
            throw new BoundaryError(reason);
        }
    };
    return capture(value, 0, code) as T;
}


/**
 * Input types that AutoEval can detect and handle
 */
export type InputType =
    | 'text'           // Plain text response
    | 'json'           // JSON response
    | 'code'           // Code/programming response
    | 'rag'            // RAG response with context
    | 'qa'             // Question-answer pair
    | 'conversation'   // Multi-turn conversation
    | 'structured'     // Structured data response
    | 'unknown';       // Cannot determine type

/**
 * Content characteristics detected in the input
 */
export interface ContentCharacteristics {
    /** Detected input type */
    inputType: InputType;
    /** Whether the response contains JSON */
    hasJson: boolean;
    /** Whether the response contains code */
    hasCode: boolean;
    /** Detected programming language (if code) */
    codeLanguage?: string;
    /** Whether context is provided */
    hasContext: boolean;
    /** Whether a reference/expected answer is provided */
    hasReference: boolean;
    /** Whether it's a question-answer format */
    isQA: boolean;
    /** Estimated response length category */
    lengthCategory: 'short' | 'medium' | 'long';
    /** Detected content domains */
    domains: string[];
}

/**
 * Input for AutoEval
 */
export interface AutoEvalInput {
    /** The query/prompt (optional) */
    query?: string;
    /** The response to evaluate */
    response: string;
    /** Context for RAG evaluations (optional) */
    context?: string | string[];
    /** Expected/reference answer (optional) */
    reference?: string;
    /** Code to evaluate (optional, for security checks) */
    code?: string;
    /** Additional metadata */
    metadata?: Record<string, unknown>;
}

/**
 * Configuration for AutoEval
 */
export interface AutoEvalConfig {
    /** Minimum confidence threshold for metric selection */
    minConfidence?: number;
    /** Maximum number of metrics to run */
    maxMetrics?: number;
    /** Specific metric categories to include */
    includeCategories?: MetricCategory[];
    /** Specific metric categories to exclude */
    excludeCategories?: MetricCategory[];
    /** Whether to run security checks on code */
    enableSecurityChecks?: boolean;
    /** Whether to run hallucination detection */
    enableHallucinationCheck?: boolean;
    /** Custom metric weights */
    metricWeights?: Record<string, number>;
    /** Threshold for passing overall evaluation */
    passThreshold?: number;
}

/**
 * Categories of metrics
 */
export type MetricCategory =
    | 'string'        // String/text metrics
    | 'json'          // JSON validation metrics
    | 'similarity'    // Similarity/comparison metrics
    | 'rag'           // RAG-specific metrics
    | 'security'      // Code security metrics
    | 'hallucination' // Hallucination detection
    | 'quality';      // General quality metrics

/**
 * A selected metric with its relevance score
 */
export interface SelectedMetric {
    /** Metric name */
    name: string;
    /** Category of the metric */
    category: MetricCategory;
    /** Confidence/relevance score (0-1) */
    confidence: number;
    /** Reason for selection */
    reason: string;
    /** Required config for this metric */
    requiredConfig?: string[];
}

/**
 * Result from a single metric execution
 */
export interface MetricExecutionResult {
    /** Metric name */
    metric: string;
    /** Category of the metric */
    category: MetricCategory;
    /** The metric result */
    result: MetricResult;
    /** Time taken to execute (ms) */
    executionTimeMs: number;
    /** Any error that occurred */
    error?: string;
    /** Completion state, independent of the numeric score. */
    status?: 'completed' | 'error' | 'not_evaluated';
    reasonCode?: string;
    executed?: boolean;
}

/**
 * Overall result from AutoEval
 */
export interface AutoEvalResult {
    /** Overall score (weighted average) */
    overallScore: number;
    /** Whether the evaluation passed */
    passed: boolean;
    /** Detected content characteristics */
    characteristics: ContentCharacteristics;
    /** Metrics that were selected */
    selectedMetrics: SelectedMetric[];
    /** Results from each metric */
    metricResults: MetricExecutionResult[];
    /** Summary of the evaluation */
    summary: string;
    /** Recommendations for improvement */
    recommendations: string[];
    /** Total execution time (ms) */
    totalExecutionTimeMs: number;
    status?: 'passed' | 'failed' | 'incomplete';
    reasonCode?: string;
    coverage?: { required: number; executed: number; completed: number; skipped: number; errored: number };
}

/**
 * Code language detection patterns
 */
export const CODE_LANGUAGE_PATTERNS: Record<string, RegExp[]> = {
    javascript: [
        /\bconst\s+\w+\s*=/,
        /\blet\s+\w+\s*=/,
        /\bfunction\s+\w+\s*\(/,
        /=>\s*\{/,
        /\bconsole\.log\(/,
        /\brequire\s*\(/,
        /\bmodule\.exports/,
    ],
    typescript: [
        /:\s*(?:string|number|boolean|any)\b/,
        /\binterface\s+\w+/,
        /\btype\s+\w+\s*=/,
        /<\w+(?:,\s*\w+)*>/,
    ],
    python: [
        /\bdef\s+\w+\s*\(/,
        /\bclass\s+\w+.*:/,
        /\bimport\s+\w+/,
        /\bfrom\s+\w+\s+import/,
        /\bif\s+__name__\s*==\s*['"]__main__['"]/,
        /:\s*$/m,
    ],
    java: [
        /\bpublic\s+class\s+\w+/,
        /\bprivate\s+\w+\s+\w+/,
        /\bSystem\.out\.println/,
        /\bvoid\s+main\s*\(/,
    ],
    sql: [
        /\bSELECT\s+.+\s+FROM\b/i,
        /\bINSERT\s+INTO\b/i,
        /\bUPDATE\s+.+\s+SET\b/i,
        /\bDELETE\s+FROM\b/i,
        /\bCREATE\s+TABLE\b/i,
    ],
    html: [
        /<html\b/i,
        /<div\b/i,
        /<span\b/i,
        /<script\b/i,
        /<style\b/i,
    ],
    css: [
        /\{[^}]*:\s*[^}]+;[^}]*\}/,
        /\.\w+\s*\{/,
        /#\w+\s*\{/,
        /@media\s+/,
    ],
    shell: [
        /^#!/,
        /\becho\s+/,
        /\bgrep\s+/,
        /\bawk\s+/,
        /\|\s*\w+/,
    ],
};
