/**
 * Base class for LLM providers with shared functionality.
 * @module local/llm/base
 */

import { snapshotBoundary } from '../autoeval/types.js';
import {
    BaseLLM,
    BaseLLMConfig,
    ChatMessage,
    GenerateOptions,
    ChatOptions,
    JudgeResult,
    JudgeInput
} from './types.js';

/**
 * Default configuration values
 */
export const DEFAULT_CONFIG: Required<BaseLLMConfig> = {
    temperature: 0.0,
    maxTokens: 1024,
    timeout: 120
};

/**
 * Abstract base class for LLM providers.
 * Provides common functionality like response parsing.
 */
export abstract class AbstractLLM implements BaseLLM {
    abstract readonly provider: string;
    abstract readonly model: string;

    protected config: Required<BaseLLMConfig>;

    constructor(config: BaseLLMConfig = {}) {
        config = snapshotBoundary(config, 'JUDGE_CONFIG_INVALID');
        if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('JUDGE_CONFIG_INVALID');
        this.config = snapshotBoundary({ ...DEFAULT_CONFIG, ...config }, 'JUDGE_CONFIG_INVALID');
        // Provider-specific fields are preserved; only base numeric fields share this range check.
        for (const field of ['temperature', 'maxTokens', 'timeout'] as const) {
            const value = this.config[field];
            if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('JUDGE_CONFIG_INVALID');
        }
    }

    abstract isAvailable(): Promise<boolean>;
    abstract generate(prompt: string, options?: GenerateOptions): Promise<string>;
    abstract chat(messages: ChatMessage[], options?: ChatOptions): Promise<string>;

    /**
     * Use LLM as a judge to evaluate a response
     */
    async judge(
        query: string,
        response: string,
        criteria: string,
        context?: string
    ): Promise<JudgeResult> {
        if (typeof query !== 'string' || typeof response !== 'string' || typeof criteria !== 'string' ||
            (context !== undefined && typeof context !== 'string')) return { score: 0, passed: false, reason: 'JUDGE_INPUT_INVALID' };
        const systemPrompt = `You are an expert evaluator. Your task is to evaluate the quality of AI-generated responses based on specific criteria.

You MUST respond with a JSON object containing:
- "score": A number between 0 and 1 (0 = completely fails, 1 = perfect)
- "passed": A boolean (true if score >= 0.5)
- "reason": A brief explanation of your evaluation

IMPORTANT: Your response must be valid JSON only, with no additional text.`;

        let userPrompt = `## Evaluation Criteria
${criteria}

## Query
${query}

## Response to Evaluate
${response}`;

        if (context) {
            userPrompt += `\n\n## Context\n${context}`;
        }

        userPrompt += `\n\n## Your Evaluation (respond with JSON only)`;

        try {
            const rawResponse = await this.chat([
                { role: 'system', content: systemPrompt },
                { role: 'user', content: userPrompt }
            ]);
            // The SDK system prompt declares 0..1, regardless of observed magnitude.
            return this.parseJudgeResponse(rawResponse, 1);
        } catch {
            return { score: 0, passed: false, reason: 'JUDGE_REQUEST_FAILED', rawResponse: undefined };
        }
    }

    /**
     * Batch evaluate multiple queries
     */
    async batchJudge(evaluations: JudgeInput[]): Promise<JudgeResult[]> {
        try { evaluations = snapshotBoundary(evaluations, 'JUDGE_INPUT_INVALID'); }
        catch { return [{ score: 0, passed: false, reason: 'JUDGE_INPUT_INVALID' }]; }
        if (!Array.isArray(evaluations) || evaluations.length === 0) return [{ score: 0, passed: false, reason: 'NO_EVIDENCE' }];
        const results: JudgeResult[] = [];

        for (const evalItem of evaluations) {
            try {
                const result = await this.judge(
                    evalItem.query,
                    evalItem.response,
                    evalItem.criteria,
                    evalItem.context
                );
                results.push(result);
            } catch (error) {
                results.push({
                    score: 0,
                    passed: false,
                    reason: 'JUDGE_REQUEST_FAILED',
                    rawResponse: undefined
                });
            }
        }

        return results;
    }

    /**
     * Parse judge response into structured result
     */
    protected parseJudgeResponse(response: string, declaredScale?: number): JudgeResult {
        if (typeof response !== 'string') return { score: 0, passed: false, reason: 'JUDGE_RESULT_INVALID' };
        const candidates: unknown[] = [];
        const starts: number[] = [];
        let quoted = false;
        let escaped = false;
        // Enumerate balanced objects, including nested objects, in every fence
        // and in prose. A quoted evaluated-output verdict is not privileged.
        for (let index = 0; index < response.length; index++) {
            const char = response[index];
            if (starts.length > 0 && quoted) {
                if (escaped) escaped = false;
                else if (char === '\\') escaped = true;
                else if (char === '"') quoted = false;
                continue;
            }
            if (starts.length > 0 && char === '"') { quoted = true; continue; }
            if (char === '{') starts.push(index);
            if (char === '}' && starts.length > 0) {
                const start = starts.pop()!;
                try {
                    const candidate = JSON.parse(response.slice(start, index + 1));
                    if (Object.prototype.hasOwnProperty.call(candidate, 'score') || Object.prototype.hasOwnProperty.call(candidate, 'passed')) candidates.push(candidate);
                } catch { /* Not a protocol object. */ }
            }
        }
        if (candidates.length > 1) return { score: 0, passed: false, reason: 'NO_EVIDENCE', rawResponse: undefined };
        if (candidates.length !== 1 || starts.length > 0) return { score: 0, passed: false, reason: 'JUDGE_RESULT_INVALID', rawResponse: undefined };
        return this.validateJudgeResult(candidates[0], response, declaredScale);
    }

    /**
     * Validate and normalize judge result
     */
    protected validateJudgeResult(parsed: any, rawResponse: string, declaredScale?: number): JudgeResult {
        try { parsed = snapshotBoundary(parsed, 'JUDGE_RESULT_INVALID'); }
        catch { return { score: 0, passed: false, reason: 'JUDGE_RESULT_INVALID', rawResponse: undefined }; }
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) ||
            typeof parsed.score !== 'number' || !Number.isFinite(parsed.score) ||
            ('passed' in parsed && typeof parsed.passed !== 'boolean')) {
            return { score: 0, passed: false, reason: 'JUDGE_RESULT_INVALID', rawResponse: undefined };
        }
        if ((declaredScale !== undefined && declaredScale !== 1 && declaredScale !== 10 && declaredScale !== 100) ||
            parsed.score < 0 || parsed.score > (declaredScale === undefined ? 100 : declaredScale)) {
            return { score: 0, passed: false, reason: 'NO_EVIDENCE', rawResponse: undefined };
        }
        const score = declaredScale === undefined ? this.normalizeScore(parsed.score) : parsed.score / declaredScale;
        const passed = score >= 0.5 && (parsed.passed === undefined || parsed.passed === true);
        return {
            score, passed,
            reason: passed ? 'JUDGE_PASSED' : 'JUDGE_FAILED',
            // Keep the key, not untrusted provider text that can echo input.
            rawResponse: undefined,
        };
    }

    /**
     * Normalize score to 0-1 range
     */
    protected normalizeScore(score: number): number {
        // Residual for provider-subclass/caller parsing with NO declared scale:
        // scores <=1 are read on 0..1, scores <=10 on 0..10; ask for 0..1.
        // SDK judge() always supplies its explicit scale and does not use this.
        if (score > 10) {
            score = score / 100; // 0-100 scale
        } else if (score > 1) {
            score = score / 10; // 0-10 scale
        }
        // Clamp to [0, 1]
        return Math.max(0, Math.min(1, score));
    }
}
