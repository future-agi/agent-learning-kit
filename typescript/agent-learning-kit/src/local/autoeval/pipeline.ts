/**
 * AutoEval Pipeline.
 * Automatically analyzes content and runs appropriate metrics.
 *
 * @module local/autoeval/pipeline
 */

import {
    AutoEvalInput,
    AutoEvalConfig,
    AutoEvalResult,
    SelectedMetric,
    MetricExecutionResult,
    ContentCharacteristics,
    snapshotBoundary
} from './types.js';
import { analyzeContent, describeCharacteristics } from './analyzer.js';
import { selectMetrics, buildMetricConfig } from './selector.js';
import { runMetric, hasMetric, getMetric } from '../metrics/index.js';

function snapshotConfig(config: AutoEvalConfig): AutoEvalConfig {
    try {
        const owned = snapshotBoundary(config, 'EVALUATION_CONFIG_INVALID');
        if (!owned || typeof owned !== 'object' || Array.isArray(owned)) throw new Error();
        const copy = { ...owned };
        for (const key of ['includeCategories', 'excludeCategories'] as const) {
            const value = copy[key];
            if (value === undefined) continue;
            if (value === null || typeof value !== 'object' || typeof value[Symbol.iterator] !== 'function') throw new Error();
            const values = Array.from(value);
            if (values.some(item => typeof item !== 'string' || !['string', 'json', 'similarity', 'rag', 'security', 'hallucination', 'quality'].includes(item))) throw new Error();
            copy[key] = values;
        }
        for (const key of ['enableSecurityChecks', 'enableHallucinationCheck'] as const) {
            if (copy[key] !== undefined && typeof copy[key] !== 'boolean') throw new Error();
        }
        for (const key of ['minConfidence', 'maxMetrics', 'passThreshold'] as const) {
            const value = copy[key];
            if (value !== undefined && (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
                (key === 'maxMetrics' ? !Number.isSafeInteger(value) : value > 1))) throw new Error();
        }
        if (copy.metricWeights !== undefined) {
            if (!copy.metricWeights || typeof copy.metricWeights !== 'object' || Array.isArray(copy.metricWeights)) throw new Error();
            copy.metricWeights = { ...copy.metricWeights };
        }
        return snapshotBoundary(copy, 'EVALUATION_CONFIG_INVALID');
    } catch { throw new Error('EVALUATION_CONFIG_INVALID'); }
}

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
 * AutoEval Pipeline - automatically selects and runs appropriate metrics.
 *
 * @example
 * ```typescript
 * const pipeline = new AutoEvalPipeline();
 *
 * // Simple text evaluation
 * const result = pipeline.evaluate({
 *     response: 'Paris is the capital of France.'
 * });
 *
 * // RAG evaluation
 * const ragResult = pipeline.evaluate({
 *     query: 'What is the capital of France?',
 *     response: 'Paris is the capital of France.',
 *     context: 'Paris is the capital and largest city of France.'
 * });
 *
 * // Code security evaluation
 * const codeResult = pipeline.evaluate({
 *     response: 'const query = "SELECT * FROM users WHERE id = " + userId;',
 *     code: 'const query = "SELECT * FROM users WHERE id = " + userId;'
 * });
 * ```
 */
export class AutoEvalPipeline {
    private config: AutoEvalConfig;

    constructor(config: AutoEvalConfig = {}) {
        this.config = snapshotBoundary({
            minConfidence: 0.5,
            maxMetrics: 10,
            enableSecurityChecks: true,
            enableHallucinationCheck: true,
            passThreshold: 0.7,
            ...snapshotConfig(config)
        }, 'EVALUATION_CONFIG_INVALID');
    }

    /**
     * Evaluate input using automatically selected metrics.
     *
     * @param input - The input to evaluate
     * @param configOverrides - Optional config overrides for this evaluation
     * @returns Evaluation result with all metric scores
     */
    evaluate(
        input: AutoEvalInput,
        configOverrides?: Partial<AutoEvalConfig>
    ): AutoEvalResult {
        ({ input, configOverrides } = snapshotBoundary({ input, configOverrides }, 'EVALUATION_INPUT_INVALID'));
        if (!input || typeof input.response !== 'string') throw new Error('EVALUATION_INPUT_INVALID');
        for (const key of ['query', 'reference', 'code'] as const) if (input[key] !== undefined && typeof input[key] !== 'string') throw new Error('EVALUATION_INPUT_INVALID');
        if (input.context !== undefined && typeof input.context !== 'string' && (!Array.isArray(input.context) || input.context.some(value => typeof value !== 'string'))) throw new Error('EVALUATION_INPUT_INVALID');
        const startTime = Date.now();
        const config = snapshotBoundary({ ...this.config, ...snapshotConfig(configOverrides === undefined ? {} : configOverrides) }, 'EVALUATION_CONFIG_INVALID');

        // Step 1: Analyze content
        const characteristics = analyzeContent(input);

        // Step 2: Select metrics
        const applicable = selectMetrics(characteristics, input, { ...config, maxMetrics: Number.MAX_SAFE_INTEGER })
            .filter(metric => metric.name !== 'context_recall' || (typeof input.reference === 'string' && input.reference.trim().length > 0));
        const cap = config.maxMetrics === undefined ? 10 : config.maxMetrics;
        // Security checks are never removed by the presentation/execution cap.
        const selectedMetrics = applicable.filter((metric, index) => metric.category === 'security' || index < cap);
        const omitted = applicable.filter(metric => !selectedMetrics.includes(metric));

        // Step 3: Execute metrics; every cap omission is an explicit record.
        const metricResults = this.executeMetrics(selectedMetrics, input, config);
        metricResults.push(...omitted.map(metric => ({ metric: metric.name, category: metric.category,
            result: { score: 0, passed: false, reason: 'METRIC_LIMIT' }, executionTimeMs: 0,
            error: 'METRIC_LIMIT', reasonCode: 'METRIC_LIMIT', status: 'not_evaluated' as const, executed: false })));

        // Step 4: Calculate overall score
        const overallScore = cap === 0 ? 0 : this.calculateOverallScore(metricResults, config);

        // Step 5: Generate summary and recommendations
        const coverage = {
            required: applicable.length,
            executed: metricResults.filter(r => r.executed).length,
            completed: metricResults.filter(r => r.status === 'completed').length,
            skipped: metricResults.filter(r => r.status === 'not_evaluated').length,
            errored: metricResults.filter(r => r.status === 'error').length,
        };
        const threshold = config.passThreshold ?? 0.7;
        const complete = cap > 0 && coverage.required > 0 && coverage.completed === coverage.required &&
            Number.isFinite(threshold) && threshold >= 0 && threshold <= 1;
        const passed = complete && metricResults.every(r => r.result.passed === true) && overallScore >= threshold;
        const status = !complete ? 'incomplete' : passed ? 'passed' : 'failed';
        const summary = !complete ? 'EVALUATION_INCOMPLETE' : !passed ? 'CHECKS_FAILED' :
            this.generateSummary(characteristics, metricResults, overallScore);
        const recommendations = this.generateRecommendations(metricResults, characteristics);

        const totalExecutionTimeMs = Date.now() - startTime;

        return {
            overallScore,
            passed,
            status,
            coverage,
            reasonCode: !complete ? 'EVALUATION_INCOMPLETE' : passed ? 'CHECKS_PASSED' : 'CHECKS_FAILED',
            characteristics,
            selectedMetrics,
            metricResults,
            summary,
            recommendations,
            totalExecutionTimeMs
        };
    }

    /**
     * Execute selected metrics
     */
    private executeMetrics(
        selectedMetrics: SelectedMetric[],
        input: AutoEvalInput,
        config: AutoEvalConfig
    ): MetricExecutionResult[] {
        const results: MetricExecutionResult[] = [];

        for (const metric of selectedMetrics) {
            const startTime = Date.now();

            let executed = false;
            const incomplete = (reasonCode: string, status: 'error' | 'not_evaluated' = 'error') => {
                results.push({
                    metric: metric.name, category: metric.category,
                    result: { score: 0, passed: false, reason: reasonCode },
                    executionTimeMs: Date.now() - startTime,
                    error: reasonCode, reasonCode, status, executed,
                });
            };
            try {
                if (!input.response?.trim()) { incomplete('NO_EVIDENCE', 'not_evaluated'); continue; }
                const weight = config.metricWeights?.[metric.name];
                if (weight !== undefined && (typeof weight !== 'number' || !Number.isFinite(weight) || weight <= 0)) {
                    incomplete('INVALID_METRIC_WEIGHT', 'not_evaluated'); continue;
                }
                const metricConfig = buildMetricConfig(metric, input);
                if (metric.name === 'context_recall') metricConfig.expected_response = input.reference;
                if (!hasMetric(metric.name)) { incomplete('METRIC_UNAVAILABLE', 'not_evaluated'); continue; }
                const required = getMetric(metric.name)?.requiredConfig ?? [];
                if (required.some(key => {
                    const value = metricConfig[key];
                    return value === null || value === undefined || (typeof value === 'string' && !value.trim()) ||
                        (Array.isArray(value) && (value.length === 0 || value.every(item => typeof item === 'string' && !item.trim())));
                })) { incomplete('NO_EVIDENCE', 'not_evaluated'); continue; }
                executed = true;
                const rawResult = runMetric(metric.name, input.response, snapshotBoundary(metricConfig, 'METRIC_CONFIG_INVALID'));
                const result = snapshotBoundary(rawResult, 'METRIC_RESULT_INVALID');
                if (!result || typeof result.score !== 'number' || !Number.isFinite(result.score) || result.score < 0 || result.score > 1 ||
                    typeof result.passed !== 'boolean' || typeof result.reason !== 'string') {
                    incomplete('METRIC_RESULT_INVALID'); continue;
                }
                // A diagnostic error is not completed validation evidence.
                if (['INVALID_SECURITY_PATTERN', 'METRIC_CONFIG_INVALID', 'METRIC_RESULT_INVALID', 'SCHEMA_MISSING', 'SCHEMA_UNSUPPORTED', 'JUDGE_RESULT_INVALID', 'JUDGE_REQUEST_FAILED'].some(code => result.reason === code)) {
                    incomplete('METRIC_VALIDATION_INCOMPLETE'); continue;
                }
                const details = result as typeof result & { claims?: unknown[]; totalClaims?: number; supportedClaims?: number; unsupportedClaims?: number };
                if (result.reason === 'NO_EVIDENCE' || details.claims?.length === 0 || details.totalClaims === 0 ||
                    (details.supportedClaims === 0 && details.unsupportedClaims === 0)) {
                    incomplete('NO_EVIDENCE', 'not_evaluated'); continue;
                }
                results.push({
                    metric: metric.name, category: metric.category,
                    result: { ...(redactDetails(result) as typeof result), score: result.score, passed: result.passed, reason: result.passed ? 'CHECK_PASSED' : 'CHECK_FAILED' },
                    executionTimeMs: Date.now() - startTime, status: 'completed', executed,
                });
            } catch {
                incomplete('METRIC_EXECUTION_FAILED');
            }
        }

        return results;
    }

    /**
     * Calculate overall weighted score
     */
    private calculateOverallScore(
        results: MetricExecutionResult[],
        config: AutoEvalConfig
    ): number {
        if (results.length === 0 || results.some(r => r.status !== 'completed')) {
            return 0;
        }

        const weights = config.metricWeights || {};
        let totalWeight = 0;
        let weightedSum = 0;

        // Category weights (default)
        const categoryWeights: Record<string, number> = {
            'security': 1.5,      // Security issues are critical
            'hallucination': 1.3, // Hallucination is important
            'rag': 1.2,           // RAG quality matters
            'similarity': 1.0,    // Standard weight
            'json': 1.0,
            'string': 0.8,
            'quality': 0.8
        };

        const metricWeights = results.map(r => weights[r.metric] ?? categoryWeights[r.category] ?? 1);
        if (metricWeights.some(w => typeof w !== 'number' || !Number.isFinite(w) || w <= 0)) return 0;
        // Normalize before summation to avoid overflowing large finite weights.
        const scale = Math.max(...metricWeights);
        results.forEach((result, index) => {
            const weight = metricWeights[index] / scale;
            totalWeight += weight;
            weightedSum += result.result.score * weight;
        });
        return totalWeight > 0 ? weightedSum / totalWeight : 0;
    }

    /**
     * Generate a human-readable summary
     */
    private generateSummary(
        characteristics: ContentCharacteristics,
        results: MetricExecutionResult[],
        overallScore: number
    ): string {
        const parts: string[] = [];

        // Content description
        parts.push(`Evaluated ${characteristics.inputType} content (${characteristics.lengthCategory} length).`);

        // Metric summary
        const successCount = results.filter(r => r.result.passed && !r.error).length;
        const failCount = results.filter(r => !r.result.passed && !r.error).length;
        const errorCount = results.filter(r => r.error).length;

        parts.push(`Ran ${results.length} metrics: ${successCount} passed, ${failCount} failed${errorCount > 0 ? `, ${errorCount} errors` : ''}.`);

        // Overall assessment
        if (overallScore >= 0.9) {
            parts.push('Excellent overall quality.');
        } else if (overallScore >= 0.7) {
            parts.push('Good overall quality with minor issues.');
        } else if (overallScore >= 0.5) {
            parts.push('Moderate quality with notable issues.');
        } else {
            parts.push('Significant quality issues detected.');
        }

        return parts.join(' ');
    }

    /**
     * Generate recommendations based on results
     */
    private generateRecommendations(
        results: MetricExecutionResult[],
        characteristics: ContentCharacteristics
    ): string[] {
        const recommendations: string[] = [];

        // Check for failed metrics and generate recommendations
        for (const result of results) {
            if (result.error) {
                continue;
            }

            if (!result.result.passed) {
                switch (result.category) {
                    case 'security':
                        if (result.metric === 'sql_injection') {
                            recommendations.push('Use parameterized queries instead of string concatenation for SQL.');
                        } else if (result.metric === 'xss_detection') {
                            recommendations.push('Sanitize user input before inserting into HTML. Use textContent instead of innerHTML.');
                        } else if (result.metric === 'secrets_detection') {
                            recommendations.push('Move hardcoded secrets to environment variables or a secrets manager.');
                        }
                        break;

                    case 'hallucination':
                        recommendations.push('Review claims for accuracy and ensure they are supported by the provided context.');
                        break;

                    case 'rag':
                        if (result.metric === 'faithfulness') {
                            recommendations.push('Ensure the response stays grounded in the provided context.');
                        } else if (result.metric === 'answer_relevance') {
                            recommendations.push('Make the response more directly address the query.');
                        } else if (result.metric === 'context_utilization') {
                            recommendations.push('Better utilize the available context in the response.');
                        }
                        break;

                    case 'json':
                        recommendations.push('Fix JSON syntax errors to produce valid JSON output.');
                        break;
                }
            }
        }

        // Add general recommendations based on characteristics
        if (characteristics.hasCode && !results.some(r => r.category === 'security')) {
            recommendations.push('Consider running security checks on code output.');
        }

        if (characteristics.hasContext && !results.some(r => r.category === 'hallucination')) {
            recommendations.push('Consider checking for hallucinations when context is provided.');
        }

        // Deduplicate
        return [...new Set(recommendations)];
    }

    /**
     * Get the characteristics of an input without running evaluation
     */
    analyzeOnly(input: AutoEvalInput): {
        characteristics: ContentCharacteristics;
        description: string;
        recommendedMetrics: SelectedMetric[];
    } {
        input = snapshotBoundary(input, 'EVALUATION_INPUT_INVALID');
        if (!input || typeof input.response !== 'string') throw new Error('EVALUATION_INPUT_INVALID');
        const characteristics = analyzeContent(input);
        const description = describeCharacteristics(characteristics);
        const recommendedMetrics = selectMetrics(characteristics, input, this.config);

        return {
            characteristics,
            description,
            recommendedMetrics
        };
    }

    /**
     * Update pipeline configuration
     */
    configure(config: Partial<AutoEvalConfig>): void {
        this.config = snapshotBoundary({ ...this.config, ...snapshotConfig(config) }, 'EVALUATION_CONFIG_INVALID');
    }

    /**
     * Get current configuration
     */
    getConfig(): AutoEvalConfig {
        return { ...this.config };
    }
}

/**
 * Create an AutoEval pipeline with default configuration
 */
export function createAutoEval(config?: AutoEvalConfig): AutoEvalPipeline {
    return new AutoEvalPipeline(config);
}

/**
 * Quick evaluation function without creating a pipeline instance
 */
export function autoEvaluate(
    input: AutoEvalInput,
    config?: AutoEvalConfig
): AutoEvalResult {
    const pipeline = new AutoEvalPipeline(config);
    return pipeline.evaluate(input);
}
