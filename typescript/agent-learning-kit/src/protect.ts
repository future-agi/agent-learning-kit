import { HttpMethod, Routes, InvalidAuthError, SDKException } from './core/index.js';
import { EvalResponseHandler, Evaluator } from './evaluator.js';
import { Templates } from './templates.js';
import { BatchRunResult } from './types.js';
import { snapshotBoundary } from './local/autoeval/types.js';

// A protection request asks for one verdict. Validate every supplied slot and
// reject extra slots rather than silently selecting a successful first row.
function protectionResult(raw: unknown, flash: boolean): { result: any; failed: boolean } {
    const response = snapshotBoundary(raw, 'PROTECT_RESULT_INVALID') as any;
    if (!response || !Array.isArray(response.eval_results) ||
        (response.status !== undefined && response.status !== 'completed' && response.status !== 'failed')) throw new Error('PROTECT_RESULT_INVALID');
    // Legacy Protect verdicts may omit output entirely. A supplied output or
    // declared kind, however, must agree with completed evaluation evidence.
    const valid = response.eval_results.map((row: any) => !!row && typeof row === 'object' && !Array.isArray(row) &&
        (!flash || typeof row.failure === 'boolean') &&
        (row.failure === undefined || typeof row.failure === 'boolean') &&
        (!Object.prototype.hasOwnProperty.call(row, 'passed') || typeof row.passed === 'boolean') &&
        (row.eval_status === undefined || row.eval_status === 'completed' || row.eval_status === 'failed') &&
        (!('status' in row) || row.status === 'completed' || row.status === 'passed' || row.status === 'failed') &&
        (row.error_message === undefined || row.error_message === null || typeof row.error_message === 'string') &&
        (!('output' in row) || (row.output !== null && row.output !== undefined && row.output !== 'Pending')) &&
        (typeof row.output !== 'number' || Number.isFinite(row.output)) &&
        (row.output_type === undefined || typeof row.output_type === 'string') &&
        (row.output_type !== 'score' || typeof row.output === 'number') &&
        (row.output_type !== 'boolean' || typeof row.output === 'boolean') &&
        (row.output_type !== 'text' || typeof row.output === 'string') &&
        (flash || (Array.isArray(row.data) && row.data.length > 0 && row.data.every((item: unknown) => typeof item === 'string'))));
    if (valid.length !== 1 || valid.some((ok: boolean) => !ok)) throw new Error('PROTECT_RESULT_INVALID');
    const result = response.eval_results[0];
    return { result, failed: response.status === 'failed' || result.failure === true || result.passed === false ||
        result.status === 'failed' || result.eval_status === 'failed' || !!result.error_message };
}

const PROTECT_FLASH_ID = '76';

export class Protect {
    public evaluator: Evaluator;
    private metric_map: Record<string, any>;

    constructor(options: { fiApiKey?: string; fiSecretKey?: string; fiBaseUrl?: string; evaluator?: Evaluator } = {}) {
        // The injected transport is an opaque capability, not a data record.
        let supplied: Evaluator | undefined;
        let credentials: Omit<typeof options, 'evaluator'>;
        try {
            supplied = options.evaluator;
            credentials = snapshotBoundary({ fiApiKey: options.fiApiKey, fiSecretKey: options.fiSecretKey, fiBaseUrl: options.fiBaseUrl }, 'PROTECT_CONFIG_INVALID');
        } catch { throw new SDKException('PROTECT_CONFIG_INVALID'); }
        if (supplied !== undefined) {
            this.evaluator = supplied;
        } else {
            const fiApiKey = process.env.FI_API_KEY || credentials.fiApiKey;
            const fiSecretKey = process.env.FI_SECRET_KEY || credentials.fiSecretKey;
            const fiBaseUrl = process.env.FI_BASE_URL || credentials.fiBaseUrl;
            if (!fiApiKey || !fiSecretKey) throw new InvalidAuthError('PROTECT_AUTH_MISSING');
            this.evaluator = new Evaluator({ fiApiKey, fiSecretKey, fiBaseUrl });
        }
        this.metric_map = {
            Toxicity: Templates.Toxicity, Tone: Templates.Tone, Sexism: Templates.Sexist,
            'Prompt Injection': Templates.PromptInjection, 'Data Privacy': Templates.DataPrivacyCompliance,
        };
    }

    /** Bound even a transport that ignores its timeout; clear timers on every exit. */
    private async withinBudget<T>(operation: () => Promise<T>, milliseconds: number): Promise<T> {
        if (!Number.isFinite(milliseconds) || milliseconds <= 0 || milliseconds > 2147483647) throw new Error('PROTECT_TIMEOUT');
        const deadline = Date.now() + milliseconds;
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
            const watchdog = new Promise<never>((_, reject) => {
                timer = setTimeout(() => reject(new Error('PROTECT_TIMEOUT')), milliseconds);
            });
            const result = await Promise.race([watchdog, Promise.resolve().then(operation)]);
            if (Date.now() >= deadline) throw new Error('PROTECT_TIMEOUT');
            return result;
        } finally {
            if (timer !== undefined) clearTimeout(timer);
        }
    }

    private async _check_rule_sync(
        rule: Record<string, any>,
        testCase: { input: string; call_type: string },
        timeoutSeconds: number
    ): Promise<[string, boolean, string | undefined, string | undefined]> {
        const templateInfo = this.metric_map[rule.metric];
        const templateConfig: Record<string, any> = { call_type: 'protect' };
        if (rule.metric === 'Data Privacy') templateConfig.check_internet = false;
        const response = await this.withinBudget(() => this.evaluator.request({
            method: HttpMethod.POST,
            url: `${this.evaluator.baseUrl}/${Routes.evaluate}`,
            json: { inputs: [testCase], config: { [templateInfo.eval_id]: templateConfig } },
            timeout: timeoutSeconds * 1000,
        }, EvalResponseHandler) as Promise<BatchRunResult>, timeoutSeconds * 1000);
        const { result, failed } = protectionResult(response, false);
        const detected: string[] = result.data;
        const triggered = failed || (rule.type === 'all'
            ? rule.contains.every((value: string) => detected.includes(value))
            : detected.some(value => rule.contains.includes(value)));
        return [rule.metric, triggered, triggered ? rule.action : undefined, triggered ? 'PROTECTION_RULE_TRIGGERED' : undefined];
    }

    public async protect(
        inputs: string,
        protectRules: Record<string, any>[] | null = null,
        action: string = 'Response cannot be generated as the input fails the checks',
        reason: boolean = false,
        timeout: number = 30000,
        useFlash: boolean = false
    ): Promise<Record<string, any>> {
        if (typeof inputs !== 'string' || !inputs.trim()) throw new SDKException('PROTECT_INPUT_INVALID');
        if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 0) throw new SDKException('PROTECT_TIMEOUT_INVALID');
        if (typeof reason !== 'boolean' || typeof useFlash !== 'boolean') throw new SDKException('PROTECT_CONFIG_INVALID');
        const startTime = Date.now();
        const deadline = startTime + timeout;
        let rules: Record<string, any>[];
        try {
            const owned = snapshotBoundary(protectRules, 'PROTECT_RULES_INVALID');
            if (owned !== null && !Array.isArray(owned)) throw new Error();
            rules = (owned ?? []).map(rule => ({ ...rule }));
        } catch { throw new SDKException('PROTECT_RULES_INVALID'); }
        const incomplete = (required: number, names: string[], reasonCode: string) => ({
            status: 'incomplete', completed_rules: [], uncompleted_rules: names, failed_rule: null,
            messages: 'PROTECTION_INCOMPLETE', reasons: reasonCode, reasonCode,
            time_taken: (Date.now() - startTime) / 1000,
            coverage: { required, executed: 0, completed: 0, skipped: required, errored: 0 },
        });

        if (useFlash) {
            if (timeout === 0) return incomplete(1, ['ProtectFlash'], 'PROTECT_TIMEOUT');
            try {
                const response = await this.withinBudget(() => this.evaluator.request({
                    method: HttpMethod.POST, url: `${this.evaluator.baseUrl}/${Routes.evaluate}`,
                    json: { inputs: [{ input: inputs, call_type: 'protect' }], config: { [PROTECT_FLASH_ID]: { call_type: 'protect' } }, protect_flash: true },
                    timeout,
                }, EvalResponseHandler) as Promise<BatchRunResult>, deadline - Date.now());
                let failed: boolean;
                try { failed = protectionResult(response, true).failed; }
                catch { return { ...incomplete(1, ['ProtectFlash'], 'PROTECT_RESULT_INVALID'), coverage: { required: 1, executed: 1, completed: 0, skipped: 0, errored: 1 } }; }
                if (Date.now() >= deadline) throw new Error('PROTECT_TIMEOUT');
                return {
                    status: failed ? 'failed' : 'passed', completed_rules: ['ProtectFlash'], uncompleted_rules: [],
                    failed_rule: failed ? 'ProtectFlash' : null,
                    messages: failed ? 'PROTECTION_RULE_TRIGGERED' : 'CHECKS_PASSED',
                    reasons: failed ? 'PROTECTION_RULE_TRIGGERED' : 'All checks passed',
                    reasonCode: failed ? 'CHECKS_FAILED' : 'CHECKS_PASSED',
                    time_taken: (Date.now() - startTime) / 1000,
                    coverage: { required: 1, executed: 1, completed: 1, skipped: 0, errored: 0 },
                };
            } catch {
                return { ...incomplete(1, ['ProtectFlash'], 'PROTECT_CHECK_FAILED'), coverage: { required: 1, executed: 1, completed: 0, skipped: 0, errored: 1 } };
            }
        }

        if (rules.length === 0) throw new SDKException('PROTECT_RULES_EMPTY');
        for (const rule of rules) {
            if (typeof rule.metric !== 'string' || !Object.prototype.hasOwnProperty.call(this.metric_map, rule.metric)) throw new SDKException('PROTECT_RULE_UNKNOWN');
            if (rule.metric === 'Tone') {
                try {
                    const contains = rule.contains;
                    if (contains === null || typeof contains !== 'object' || typeof contains[Symbol.iterator] !== 'function') throw new Error();
                    rule.contains = Array.from(contains);
                } catch { throw new SDKException('PROTECT_RULE_INVALID'); }
                if (rule.contains.length === 0 || rule.contains.some((v: unknown) => typeof v !== 'string' || !v)) throw new SDKException('PROTECT_RULE_INVALID');
                if (rule.type !== undefined && rule.type !== 'any' && rule.type !== 'all') throw new SDKException('PROTECT_RULE_INVALID');
                rule.type = rule.type ?? 'any';
            } else {
                if (rule.contains !== undefined || rule.type !== undefined) throw new SDKException('PROTECT_RULE_INVALID');
                rule.contains = ['Failed'];
                rule.type = 'any';
            }
            rule.action = rule.action || action;
        }

        const completed: string[] = [];
        const uncompleted: string[] = [];
        let failedRule: string | undefined;
        let failureMessage: string | undefined;
        let executed = 0;
        let errored = 0;
        // Continue after a triggered rule so all requested coverage is accounted for.
        for (let offset = 0; offset < rules.length; offset += 5) {
            const batch = rules.slice(offset, offset + 5);
            const outcomes = await Promise.all(batch.map(async rule => {
                const remaining = deadline - Date.now();
                if (remaining <= 0) return { metric: rule.metric, outcome: null };
                executed++;
                try {
                    return { metric: rule.metric, outcome: await this._check_rule_sync(rule, { input: inputs, call_type: 'protect' }, remaining / 1000) };
                } catch {
                    errored++;
                    return { metric: rule.metric, outcome: null };
                }
            }));
            for (const { metric, outcome } of outcomes) {
                if (!outcome) { uncompleted.push(metric); continue; }
                completed.push(metric);
                if (outcome[1] && !failedRule) { failedRule = metric; failureMessage = outcome[2]; }
            }
        }
        if (Date.now() >= deadline) {
            uncompleted.push(...completed.splice(0));
        }
        const complete = completed.length === rules.length;
        const status = failedRule ? 'failed' : complete ? 'passed' : 'incomplete';
        const reasonCode = !complete ? 'PROTECTION_INCOMPLETE' : failedRule ? 'PROTECTION_RULE_TRIGGERED' : 'CHECKS_PASSED';
        return {
            status, completed_rules: [...new Set(completed)], uncompleted_rules: [...new Set(uncompleted)],
            failed_rule: failedRule ?? null,
            messages: !complete ? 'PROTECTION_INCOMPLETE' : failedRule ? 'PROTECTION_RULE_TRIGGERED' : 'CHECKS_PASSED',
            ...(reason ? { reasons: complete && !failedRule ? 'All checks passed' : reasonCode } : {}),
            reasonCode, time_taken: (Date.now() - startTime) / 1000,
            coverage: { required: rules.length, executed, completed: completed.length, skipped: rules.length - executed, errored },
        };
    }
}

/** Convenience function to evaluate input strings against protection rules. */
export const protect = (
    inputs: string,
    protectRules: Record<string, any>[] | null,
    action?: string,
    reason?: boolean,
    timeout?: number,
    useFlash?: boolean
): Promise<Record<string, any>> => {
    const protectClient = new Protect();
    return protectClient.protect(inputs, protectRules, action, reason, timeout, useFlash);
};
