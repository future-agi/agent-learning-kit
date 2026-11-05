/**
 * Streaming Evaluator.
 * Evaluates responses in real-time as chunks stream in.
 *
 * @module local/streaming/evaluator
 */

import {
    StreamingEvalConfig,
    StreamingState,
    StreamingEventHandler,
    StreamingEvalEvent,
    ChunkEvent,
    MetricUpdateEvent,
    ThresholdAlertEvent,
    CompleteEvent,
    ErrorEvent,
    canStreamMetric
} from './types.js';
import { runMetric, hasMetric, getMetric, MetricResult } from '../metrics/index.js';
import { snapshotBoundary } from '../autoeval/types.js';

// Preserve metric detail keys/counts without returning input-derived text.
function redactDetails(value: unknown, depth = 0): unknown {
    if (depth > 32) return undefined;
    if (typeof value === 'string') return '[REDACTED]';
    if (Array.isArray(value)) return value.map(item => redactDetails(item, depth + 1));
    if (value && typeof value === 'object') return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [key, redactDetails(item, depth + 1)]));
    return value;
}

/**
 * Streaming Evaluator - evaluates responses as they stream in.
 *
 * @example
 * ```typescript
 * const evaluator = new StreamingEvaluator({
 *     metrics: ['contains', 'secrets_detection'],
 *     thresholds: { secrets_detection: 1.0 },
 *     query: 'Write a greeting function'
 * });
 *
 * // Subscribe to events
 * evaluator.on((event) => {
 *     if (event.type === 'threshold_alert') {
 *         console.log('Alert:', event.message);
 *     }
 * });
 *
 * // Feed chunks as they arrive
 * for await (const chunk of streamingResponse) {
 *     evaluator.addChunk(chunk);
 * }
 *
 * // Complete and get final results
 * const results = evaluator.complete();
 * ```
 */
export class StreamingEvaluator {
    private config: Required<StreamingEvalConfig>;
    private state: StreamingState;
    private handlers: StreamingEventHandler[] = [];
    private previousScores: Map<string, number> = new Map();
    private metricErrors = new Map<string, string>();
    private attempted = new Set<string>();
    private finalResults = new Map<string, MetricResult>();
    private qualityFailures = new Map<string, MetricResult>();

    constructor(config: StreamingEvalConfig) {
        config = snapshotBoundary(config, 'STREAM_CONFIG_INVALID');
        if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('STREAM_CONFIG_INVALID');
        for (const key of ['minCharsBeforeEval', 'evalFrequency', 'maxAccumulatedLength'] as const) {
            const value = config[key];
            if (value !== undefined && (typeof value !== 'number' || !Number.isSafeInteger(value) || value < (key === 'minCharsBeforeEval' ? 0 : 1))) throw new Error('STREAM_CONFIG_INVALID');
        }
        if (config.emitChunks !== undefined && typeof config.emitChunks !== 'boolean') throw new Error('STREAM_CONFIG_INVALID');
        for (const key of ['query', 'reference'] as const) if (config[key] !== undefined && typeof config[key] !== 'string') throw new Error('STREAM_CONFIG_INVALID');
        if (config.context !== undefined && typeof config.context !== 'string' &&
            (!Array.isArray(config.context) || config.context.some(value => typeof value !== 'string'))) throw new Error('STREAM_CONFIG_INVALID');
        if (config.thresholds !== undefined && (!config.thresholds || typeof config.thresholds !== 'object' || Array.isArray(config.thresholds))) throw new Error('STREAM_CONFIG_INVALID');
        this.config = {
            metrics: this.snapshotMetrics(config.metrics),
            minCharsBeforeEval: config.minCharsBeforeEval ?? 50,
            evalFrequency: config.evalFrequency ?? 1,
            thresholds: { ...config.thresholds },
            emitChunks: config.emitChunks ?? true,
            maxAccumulatedLength: config.maxAccumulatedLength ?? 100000,
            context: config.context ?? '',
            query: config.query ?? '',
            reference: config.reference ?? ''
        };

        this.state = {
            accumulated: '',
            chunkCount: 0,
            startTime: Date.now(),
            lastEvalTime: 0,
            scores: new Map(),
            active: true
        };

        for (const threshold of Object.values(this.config.thresholds)) {
            if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) throw new Error('STREAM_CONFIG_INVALID');
        }
        this.config = snapshotBoundary(this.config, 'STREAM_CONFIG_INVALID');
        // Validate metrics
        this.validateMetrics();
    }

    private snapshotMetrics(value: string[]): string[] {
        try {
            if (value === null || typeof value !== 'object' || typeof value[Symbol.iterator] !== 'function') throw new Error();
            const metrics = Array.from(value);
            if (metrics.some(name => typeof name !== 'string' || !name.trim())) throw new Error();
            return [...new Set(metrics)];
        } catch { throw new Error('STREAM_CONFIG_INVALID'); }
    }

    /**
     * Validate that all configured metrics exist and can stream
     */
    private validateMetrics(): void {
        for (const metric of this.config.metrics) {
            if (!hasMetric(metric)) this.metricErrors.set(metric, 'METRIC_UNAVAILABLE');
        }
    }

    /**
     * Register an event handler
     */
    on(handler: StreamingEventHandler): () => void {
        this.handlers.push(handler);

        // Return unsubscribe function
        return () => {
            const index = this.handlers.indexOf(handler);
            if (index > -1) {
                this.handlers.splice(index, 1);
            }
        };
    }

    /**
     * Emit an event to all handlers
     */
    private emit(event: StreamingEvalEvent): void {
        for (const handler of this.handlers) {
            try {
                handler(event);
            } catch (error) {
                console.error('STREAM_HANDLER_FAILED');
            }
        }
    }

    /**
     * Add a chunk of content
     */
    addChunk(chunk: string): void {
        if (typeof chunk !== 'string') throw new Error('STREAM_INPUT_INVALID');
        if (!this.state.active) {
            throw new Error('Cannot add chunk to completed stream');
        }

        // Append chunk
        this.state.accumulated += chunk;
        this.state.chunkCount++;

        // The retained payload is a capped prefix, not evidence of full-stream
        // coverage. Dropping any text permanently makes this session incomplete.
        if (this.state.accumulated.length > this.config.maxAccumulatedLength) {
            this.state.accumulated = this.state.accumulated.slice(0, this.config.maxAccumulatedLength);
            for (const metric of this.config.metrics) this.metricErrors.set(metric, 'TRUNCATED');
        }

        // Emit chunk event
        if (this.config.emitChunks) {
            const chunkEvent: ChunkEvent = {
                type: 'chunk',
                timestamp: Date.now(),
                chunk,
                accumulated: this.state.accumulated,
                chunkCount: this.state.chunkCount
            };
            this.emit(chunkEvent);
        }

        // Check if we should evaluate
        if (this.shouldEvaluate()) {
            this.evaluate();
        }
    }

    /**
     * Determine if we should run evaluation
     */
    private shouldEvaluate(): boolean {
        // Check minimum characters
        if (this.state.accumulated.length < this.config.minCharsBeforeEval) {
            return false;
        }

        // Check frequency
        if (this.state.chunkCount % this.config.evalFrequency !== 0) {
            return false;
        }

        return true;
    }

    /**
     * Run evaluation on current accumulated content
     */
    private evaluate(): void {
        this.state.lastEvalTime = Date.now();

        for (const metricName of this.config.metrics) {
            const fail = (reasonCode: string) => {
                this.metricErrors.set(metricName, reasonCode);
                this.state.scores.delete(metricName);
                this.finalResults.set(metricName, { score: 0, passed: false, reason: reasonCode });
                this.emit({ type: 'error', timestamp: Date.now(), message: reasonCode });
            };
            const previousError = this.metricErrors.get(metricName);
            if (previousError) { fail(previousError); continue; }
            if (!this.state.accumulated.trim()) {
                // An empty prefix has not run a check. Only final absence of
                // evidence is terminal; a real NO_EVIDENCE metric result latches.
                if (!this.state.active) fail('NO_EVIDENCE');
                continue;
            }
            try {
                // Keep caller-facing result/threshold keys, but route the adapter
                // and execution through the same case-insensitive registry identity.
                const metricId = metricName.toLowerCase();
                const metricConfig = this.buildMetricConfig(metricId);
                const required = getMetric(metricId)?.requiredConfig ?? [];
                if (required.some(key => {
                    const value = metricConfig[key];
                    return value === null || value === undefined || (typeof value === 'string' && !value.trim()) ||
                        (Array.isArray(value) && (value.length === 0 || value.every(item => typeof item === 'string' && !item.trim())));
                })) { fail('NO_EVIDENCE'); continue; }
                this.attempted.add(metricName);
                const rawResult = runMetric(metricId, this.state.accumulated, metricConfig);
                const result = snapshotBoundary(rawResult, 'METRIC_RESULT_INVALID');
                if (!result || typeof result.score !== 'number' || !Number.isFinite(result.score) || result.score < 0 || result.score > 1 ||
                    typeof result.passed !== 'boolean' || typeof result.reason !== 'string') {
                    fail('METRIC_RESULT_INVALID'); continue;
                }
                if (['INVALID_SECURITY_PATTERN', 'METRIC_CONFIG_INVALID', 'METRIC_RESULT_INVALID', 'SCHEMA_MISSING', 'SCHEMA_UNSUPPORTED', 'JUDGE_RESULT_INVALID', 'JUDGE_REQUEST_FAILED'].some(code => result.reason === code)) {
                    fail('METRIC_VALIDATION_INCOMPLETE'); continue;
                }
                const details = result as MetricResult & { claims?: unknown[]; totalClaims?: number; supportedClaims?: number; unsupportedClaims?: number };
                if (result.reason === 'NO_EVIDENCE' || details.claims?.length === 0 || details.totalClaims === 0 ||
                    (details.supportedClaims === 0 && details.unsupportedClaims === 0)) {
                    fail('NO_EVIDENCE'); continue;
                }
                const safeResult = { ...(redactDetails(result) as MetricResult), score: result.score, passed: result.passed,
                    reason: result.passed ? 'CHECK_PASSED' : 'CHECK_FAILED' };
                if (!result.passed && !this.qualityFailures.has(metricName)) this.qualityFailures.set(metricName, safeResult);
                const effectiveResult = this.qualityFailures.get(metricName) ?? safeResult;
                this.finalResults.set(metricName, effectiveResult);

                // Get previous score
                const previousScore = this.state.scores.get(metricName);

                // Update score
                this.state.scores.set(metricName, effectiveResult.score);

                // Emit metric update
                const updateEvent: MetricUpdateEvent = {
                    type: 'metric_update',
                    timestamp: Date.now(),
                    metric: metricName,
                    score: effectiveResult.score,
                    previousScore,
                    delta: previousScore !== undefined ? effectiveResult.score - previousScore : undefined,
                    passing: effectiveResult.passed
                };
                this.emit(updateEvent);

                // Check thresholds
                this.checkThreshold(metricName, effectiveResult.score, previousScore);

            } catch {
                fail('METRIC_EXECUTION_FAILED');
            }
        }
    }

    /**
     * Build metric configuration from streaming config
     */
    private buildMetricConfig(metricName: string): Record<string, unknown> {
        const config: Record<string, unknown> = {};

        // Add context fields for RAG metrics
        if (this.config.context) {
            config.context = this.config.context;
        }
        if (this.config.query) {
            config.query = this.config.query;
        }
        if (this.config.reference) {
            config.reference = this.config.reference;
            if (metricName === 'context_recall') config.expected_response = this.config.reference;
        }

        // Response is the accumulated content
        config.response = this.state.accumulated;

        // For code security metrics
        config.code = this.state.accumulated;

        return snapshotBoundary(config, 'STREAM_CONFIG_INVALID');
    }

    /**
     * Check if a threshold was crossed
     */
    private checkThreshold(metricName: string, currentScore: number, previousScore?: number): void {
        const threshold = this.config.thresholds[metricName];
        if (threshold === undefined) {
            return;
        }

        // Check if we crossed the threshold
        const wasAbove = previousScore === undefined || previousScore >= threshold;
        const isAbove = currentScore >= threshold;

        if (wasAbove !== isAbove) {
            const direction = isAbove ? 'above' : 'below';
            const alertEvent: ThresholdAlertEvent = {
                type: 'threshold_alert',
                timestamp: Date.now(),
                metric: metricName,
                score: currentScore,
                threshold,
                direction,
                message: isAbove ? 'THRESHOLD_ABOVE' : 'THRESHOLD_BELOW'
            };
            this.emit(alertEvent);
        }
    }

    /**
     * Complete the streaming session and get final results
     */
    complete(): CompleteEvent {
        if (!this.state.active) {
            throw new Error('Stream already completed');
        }

        this.state.active = false;

        // Run final evaluation
        this.evaluate();

        // Use the same final execution that emitted updates; never rerun and
        // average away a prior error. Every requested metric has an entry.
        const finalResults = new Map(this.finalResults);
        const required = this.config.metrics.length;
        const complete = required > 0 && this.metricErrors.size === 0;
        const passed = complete && [...finalResults.values()].every(result => result.passed === true);
        const coverage = {
            required, executed: this.attempted.size,
            completed: required - this.metricErrors.size,
            skipped: this.config.metrics.filter(name => !this.attempted.has(name)).length,
            errored: [...this.metricErrors.keys()].filter(name => this.attempted.has(name)).length,
        };

        const completeEvent: CompleteEvent = {
            type: 'complete',
            timestamp: Date.now(),
            finalResponse: this.state.accumulated,
            totalChunks: this.state.chunkCount,
            finalResults,
            status: !complete ? 'incomplete' : passed ? 'passed' : 'failed',
            reasonCode: !complete ? 'EVALUATION_INCOMPLETE' : passed ? 'CHECKS_PASSED' : 'CHECKS_FAILED',
            coverage,
            metricErrors: [...this.metricErrors].map(([metric, reasonCode]) => ({ metric, reasonCode })),
            elapsedMs: Date.now() - this.state.startTime
        };

        this.emit(completeEvent);
        return completeEvent;
    }

    /**
     * Get the current accumulated response
     */
    getAccumulated(): string {
        return this.state.accumulated;
    }

    /**
     * Get current scores
     */
    getScores(): Map<string, number> {
        return new Map(this.state.scores);
    }

    /**
     * Get current state
     */
    getState(): Readonly<StreamingState> {
        return { ...this.state, scores: new Map(this.state.scores) };
    }

    /**
     * Check if the stream is still active
     */
    isActive(): boolean {
        return this.state.active;
    }

    /**
     * Reset the evaluator for reuse
     */
    reset(): void {
        this.state = {
            accumulated: '',
            chunkCount: 0,
            startTime: Date.now(),
            lastEvalTime: 0,
            scores: new Map(),
            active: true
        };
        this.previousScores.clear();
        this.metricErrors.clear();
        this.finalResults.clear();
        this.qualityFailures.clear();
        this.attempted.clear();
        this.validateMetrics();
    }
}

/**
 * Create a streaming evaluator with common defaults
 */
export function createStreamingEvaluator(config: StreamingEvalConfig): StreamingEvaluator {
    return new StreamingEvaluator(config);
}

/**
 * Evaluate a stream of chunks (async generator)
 */
export async function evaluateStream(
    chunks: AsyncIterable<string>,
    config: StreamingEvalConfig
): Promise<CompleteEvent> {
    const evaluator = new StreamingEvaluator(config);

    for await (const chunk of chunks) {
        evaluator.addChunk(chunk);
    }

    return evaluator.complete();
}

/**
 * Create an async generator that evaluates chunks and yields events
 */
export async function* streamWithEvaluation(
    chunks: AsyncIterable<string>,
    config: StreamingEvalConfig
): AsyncGenerator<StreamingEvalEvent, CompleteEvent> {
    config = snapshotBoundary(config, 'STREAM_CONFIG_INVALID');
    const evaluator = new StreamingEvaluator({
        ...config,
        emitChunks: true
    });

    const events: StreamingEvalEvent[] = [];
    evaluator.on((event) => events.push(event));

    for await (const chunk of chunks) {
        evaluator.addChunk(chunk);

        // Yield any accumulated events
        while (events.length > 0) {
            yield events.shift()!;
        }
    }

    const completeEvent = evaluator.complete();

    // Yield any remaining events
    while (events.length > 0) {
        yield events.shift()!;
    }

    return completeEvent;
}
