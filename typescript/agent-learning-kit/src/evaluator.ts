import {
    APIKeyAuth,
    ResponseHandler,
    HttpMethod,
    RequestConfig,
    Routes,
    InvalidAuthError,
    DatasetNotFoundError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    SDKException,
    InvalidValueType,
    MissingRequiredKey,
    AUTH_ENVVAR_NAME,
} from './core/index.js';
import { mapInputsToBackend } from './core/cloudRegistry.js';
import { snapshotBoundary } from './local/autoeval/types.js';


function coerceToApiInput(value: unknown): unknown {
    if (value === null || value === undefined) return value;
    if (typeof value === "string") return [value];
    if (Array.isArray(value)) {
        if (value.every(v => typeof v === "string")) return value;
        if (value.every(v => Array.isArray(v) && v.every(x => typeof x === "string"))) return value;
        return JSON.stringify(value);
    }
    if (typeof value === "object") return JSON.stringify(value);
    return value;
}
import { AxiosResponse } from 'axios';

import { Execution, normalizeStatus } from './execution.js';
import { EvalTemplate } from './templates.js';
import {
    BatchRunResult,
    EvalResult,
    EvalResultMetric,
    EvaluatorConfig,
    EvaluateOptions,
    PipelineEvalData,
    PipelineResult
} from './types.js';


function responseData(raw: any, code: string): any {
    try {
        const data = raw?.data;
        return snapshotBoundary(data === undefined ? raw : data, code);
    } catch { throw new Error(code); }
}

// Operate only on captured records. Envelopes have verdict state but need not
// carry output themselves; expanding their children must never erase that state.
function terminalState(row: any): 'completed' | 'failed' | 'incomplete' {
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        (row.eval_status !== undefined && row.eval_status !== 'completed' && row.eval_status !== 'failed') ||
        (row.failure !== undefined && typeof row.failure !== 'boolean') ||
        ('passed' in row && typeof row.passed !== 'boolean') ||
        ('status' in row && row.status !== 'completed' && row.status !== 'passed' && row.status !== 'failed') ||
        (row.error_message !== undefined && row.error_message !== null && typeof row.error_message !== 'string')) return 'incomplete';
    return row.failure === true || row.passed === false || row.status === 'failed' ||
        row.eval_status === 'failed' || !!row.error_message ? 'failed' : 'completed';
}

function validResultOutput(row: any): boolean {
    return row.output !== null && row.output !== undefined &&
        (typeof row.output !== 'number' || Number.isFinite(row.output)) &&
        (row.output_type === undefined || typeof row.output_type === 'string') &&
        (row.output_type !== 'score' || typeof row.output === 'number') &&
        (row.output_type !== 'boolean' || typeof row.output === 'boolean') &&
        (row.output_type !== 'text' || typeof row.output === 'string');
}

// Only documented payload channels retain backend content. Diagnostics are fixed.
function completedBatch(raw: any, warnings: NonNullable<BatchRunResult['warnings']> = []): BatchRunResult {
    try { raw = snapshotBoundary(raw, 'EVALUATION_RESULT_INVALID'); }
    catch { raw = undefined; }
    let incomplete = false;
    let failed = false;
    const invalid = (): EvalResult => ({ output: null, failure: true, reason: 'EVALUATION_RESULT_INVALID', runtime: 0 });
    const status = raw?.status;
    if (status !== undefined && (typeof status !== 'string' ||
        (status !== 'completed' && status !== 'passed' && status !== 'failed'))) incomplete = true;
    if (status === 'failed') failed = true;
    const rows = Array.isArray(raw?.eval_results) ? Array.from(raw.eval_results) : [];
    if (rows.length === 0) incomplete = true;
    const eval_results = rows.map((value: any): EvalResult => {
        const state = terminalState(value);
        if (state === 'incomplete' || !validResultOutput(value) || value.output === 'Pending') {
            incomplete = true;
            return invalid();
        }
        const row = value;
        const rowFailed = state === 'failed';
        failed = failed || rowFailed;
        return {
            name: row.name, output: row.output, data: row.data, error_localizer: row.error_localizer,
            reason: rowFailed ? 'EVALUATION_FAILED' : 'EVALUATION_COMPLETED', runtime: row.runtime ?? 0,
            output_type: row.output_type, eval_id: row.eval_id, model: row.model,
            error_localizer_enabled: row.error_localizer_enabled, failure: rowFailed,
            metadata: row.metadata && typeof row.metadata === 'object' && !Array.isArray(row.metadata)
                ? { usage: row.metadata.usage ?? {}, cost: row.metadata.cost ?? {}, explanation: {} } : undefined,
            metrics: row.metrics,
        };
    });
    return {
        eval_results: eval_results.length > 0 ? eval_results : [invalid()], warnings,
        status: incomplete ? 'incomplete' : failed ? 'failed' : 'completed',
        reasonCode: incomplete ? 'EVALUATION_INCOMPLETE' : failed ? 'EVALUATION_FAILED' : 'EVALUATION_COMPLETED',
    };
}

/**
 * Handles responses for evaluation requests
 */
export class EvalResponseHandler extends ResponseHandler<BatchRunResult, any> {
    public static _parseSuccess(response: AxiosResponse): BatchRunResult {
        // Revamped backend (post 2026-04-12) returns pure snake_case:
        //   { result: [{ evaluations: [{ name, reason, runtime, output,
        //     output_type, eval_id, model?, error_localizer_enabled?,
        //     error_localizer? }] }] }
        // Async / single-row paths sometimes return the eval wrapped in
        // { eval_status, result: <eval> } — handle both.
        let data: any;
        try { data = snapshotBoundary(response.data, 'EVALUATION_RESULT_INVALID') || {}; }
        catch { throw new Error('EVALUATION_RESULT_INVALID'); }
        if (data.status === false) throw new Error('EVALUATION_BACKEND_REJECTED');
        if (data.status !== undefined && typeof data.status !== 'boolean') throw new Error('EVALUATION_RESULT_INVALID');
        const evalResults: (EvalResult | null)[] = [];

        const topResult = data.result;
        const outerList = Array.isArray(topResult)
            ? topResult
            : topResult && typeof topResult === "object"
                ? [topResult]
                : [];

        for (const item of outerList) {
            if (!item || typeof item !== 'object' || Array.isArray(item)) {
                evalResults.push({ output: null, failure: true, reason: 'EVALUATION_RESULT_INVALID', runtime: 0 });
                continue;
            }
            const wrapped = item.eval_status !== undefined && 'result' in item ? item.result : item;
            if (!wrapped || typeof wrapped !== 'object' || Array.isArray(wrapped)) {
                evalResults.push({ output: null, failure: true, reason: 'EVALUATION_RESULT_INVALID', runtime: 0 });
                continue;
            }
            const parentStates = [terminalState(item), terminalState(wrapped)];
            const parentIncomplete = parentStates.includes('incomplete');
            const parentFailed = parentStates.includes('failed');
            // A declared malformed/empty child group occupies an invalid slot;
            // do not fall back to an unrelated successful output on its parent.
            const evaluations = 'evaluations' in wrapped
                ? Array.isArray(wrapped.evaluations) && wrapped.evaluations.length > 0 ? wrapped.evaluations : [null]
                : [wrapped];

            for (const evaluation of evaluations) {
                const state = terminalState(evaluation);
                const output = evaluation && typeof evaluation === 'object'
                    ? 'output' in evaluation ? evaluation.output : evaluation.value : undefined;
                if (parentIncomplete || state === 'incomplete' ||
                    ('failure' in evaluation && typeof evaluation.failure !== 'boolean') ||
                    !validResultOutput({ output, output_type: evaluation.output_type })) {
                    evalResults.push({ output: null, failure: true, reason: 'EVALUATION_RESULT_INVALID', runtime: 0 });
                    continue;
                }
                const failed = parentFailed || state === 'failed';

                // Legacy metadata passthrough (usage / cost / explanation)
                // — still returned by some v1 paths and consumed by
                // protect.ts.
                let legacyMetadata: Record<string, any> | undefined;
                if (evaluation.metadata) {
                    let metadata: any = evaluation.metadata;
                    if (typeof metadata === "string") {
                        try { metadata = JSON.parse(metadata); } catch { /* ignore */ }
                    }
                    if (metadata && typeof metadata === "object") {
                        legacyMetadata = {
                            usage: metadata.usage ?? {},
                            cost: metadata.cost ?? {},
                            explanation: {},
                        };
                    }
                }

                evalResults.push({
                    name: evaluation.name ?? "",
                    output,
                    reason: output === 'Pending' ? 'EVALUATION_INCOMPLETE' : failed ? 'EVALUATION_FAILED' : 'EVALUATION_COMPLETED',
                    runtime: evaluation.runtime ?? 0,
                    output_type: evaluation.output_type,
                    eval_id: evaluation.eval_id,
                    model: evaluation.model,
                    error_localizer_enabled: evaluation.error_localizer_enabled,
                    error_localizer: evaluation.error_localizer ?? null,
                    // Legacy v1 fields (optional, used by Protect)
                    data: evaluation.data,
                    failure: failed ? true : evaluation.failure,
                    metadata: legacyMetadata,
                    metrics: Array.isArray(evaluation.metrics)
                        ? evaluation.metrics.map((m: any): EvalResultMetric => ({
                              id: m.id,
                              value: m.value,
                          }))
                        : undefined,
                });
            }
        }

        return { eval_results: evalResults,
            ...(evalResults.length === 0 || evalResults.some(row => row?.output === 'Pending' || row?.output === null || row?.output === undefined)
                ? { status: 'incomplete' as const, reasonCode: 'EVALUATION_INCOMPLETE' as const } : {}) } as BatchRunResult;
    }

    public static _handleError(response: AxiosResponse): never {
        if (response.status === 400) {
            throw new Error(
                'EVALUATION_BAD_REQUEST'
            );
        } else if (response.status === 403) {
            throw new InvalidAuthError();
        } else {
            throw new Error(
                'EVALUATION_REQUEST_FAILED'
            );
        }
    }
}

/**
 * Handles responses for evaluation info requests
 */
export class EvalInfoResponseHandler extends ResponseHandler<Record<string, any>, any> {
    public static _parseSuccess(response: AxiosResponse): Record<string, any> {
        const data = responseData(response, 'EVALUATION_INFO_INVALID');
        if (data?.result && typeof data.result === 'object') {
            return data.result;
        } else {
            throw new Error('EVALUATION_INFO_INVALID');
        }
    }

    public static _handleError(response: AxiosResponse): never {
        if (response.status === 400) {
            // In TypeScript with axios, it's more common to let the caller handle response.data
            throw new Error('EVALUATION_INFO_BAD_REQUEST');
        }
        if (response.status === 403) {
            throw new InvalidAuthError();
        }
        throw new Error('EVALUATION_INFO_REQUEST_FAILED');
    }
}

/**
 * Client for evaluating LLM test cases
 * Aligned with Python SDK API
 */
export class Evaluator extends APIKeyAuth {
    private readonly maxWorkers: number;
    private evalInfoCache = new Map<string, Record<string, any>>();

    // Catch only the transport await: validation/mapping errors outside this
    // boundary retain their contracts. Never attach the original error/cause.
    private async requestWithFixedDiagnostics(config: RequestConfig, handler?: typeof ResponseHandler): Promise<any> {
        try { return await (handler === undefined ? this.request(config) : this.request(config, handler)); }
        catch (error) {
            let ErrorType: new (message?: string) => Error = Error;
            try {
                if (error instanceof InvalidAuthError) ErrorType = InvalidAuthError;
                else if (error instanceof DatasetNotFoundError) ErrorType = DatasetNotFoundError;
                else if (error instanceof RateLimitError) ErrorType = RateLimitError;
                else if (error instanceof ServerError) ErrorType = ServerError;
                else if (error instanceof ServiceUnavailableError) ErrorType = ServiceUnavailableError;
                else if (error instanceof SDKException) ErrorType = SDKException;
            } catch { /* A hostile rejected value is not a diagnostic channel. */ }
            throw new ErrorType('EVALUATION_REQUEST_FAILED');
        }
    }

    // Platform credentials (Langfuse)
    private readonly langfuseSecretKey?: string;
    private readonly langfusePublicKey?: string;
    private readonly langfuseHost?: string;

    constructor(options: EvaluatorConfig = {}) {
        options = snapshotBoundary(options, 'EVALUATION_CONFIG_INVALID');
        if (!options || typeof options !== 'object' || Array.isArray(options)) throw new Error('EVALUATION_CONFIG_INVALID');
        for (const key of ['fiApiKey', 'fiSecretKey', 'fiBaseUrl', 'langfuseSecretKey', 'langfusePublicKey', 'langfuseHost'] as const) {
            if (options[key] !== undefined && typeof options[key] !== 'string') throw new Error('EVALUATION_CONFIG_INVALID');
        }
        for (const key of ['timeout', 'maxQueue', 'maxWorkers'] as const) {
            const value = options[key];
            if (value !== undefined && (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
                (key !== 'timeout' && !Number.isSafeInteger(value)) || (key === 'maxWorkers' && value === 0))) throw new Error('EVALUATION_CONFIG_INVALID');
        }
        const fiApiKey = process.env.FI_API_KEY || options.fiApiKey;
        const fiSecretKey = process.env.FI_SECRET_KEY || options.fiSecretKey;
        const fiBaseUrl = process.env.FI_BASE_URL || options.fiBaseUrl;

        super({ ...options, fiApiKey, fiSecretKey, fiBaseUrl });
        this.maxWorkers = options.maxWorkers || 8;

        // Platform credentials
        this.langfuseSecretKey = options.langfuseSecretKey || process.env.LANGFUSE_SECRET_KEY;
        this.langfusePublicKey = options.langfusePublicKey || process.env.LANGFUSE_PUBLIC_KEY;
        this.langfuseHost = options.langfuseHost || process.env.LANGFUSE_HOST;
    }

    public async evaluate(
        evalTemplates: string | EvalTemplate | (string | EvalTemplate)[],
        inputs: Record<string, string | string[]>,
        options: EvaluateOptions = {}
    ): Promise<BatchRunResult> {
        ({ evalTemplates, inputs, options } = snapshotBoundary({ evalTemplates, inputs, options }, 'EVALUATION_CONFIG_INVALID'));
        if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('REQUIRED_INPUT_MISSING');
        if (!options || typeof options !== 'object' || Array.isArray(options)) throw new Error('EVALUATION_CONFIG_INVALID');
        if (options.timeout !== undefined && (typeof options.timeout !== 'number' || !Number.isFinite(options.timeout) || options.timeout < 0)) throw new Error('EVALUATION_CONFIG_INVALID');
        for (const key of ['traceEval', 'isAsync', 'errorLocalizer'] as const) if (options[key] !== undefined && typeof options[key] !== 'boolean') throw new Error('EVALUATION_CONFIG_INVALID');
        for (const key of ['modelName', 'customEvalName', 'platform'] as const) if (options[key] !== undefined && typeof options[key] !== 'string') throw new Error('EVALUATION_CONFIG_INVALID');
        const {
            timeout,
            modelName,
            customEvalName,
            traceEval: initialTraceEval = false,
            platform,
            isAsync = false,
            errorLocalizer = false,
            evalConfig
        } = options;

        if (typeof evalTemplates !== 'string' && evalTemplates !== null && typeof evalTemplates === 'object' &&
            typeof (evalTemplates as any)[Symbol.iterator] === 'function') {
            evalTemplates = Array.from(evalTemplates as (string | EvalTemplate)[]);
            if (evalTemplates.length === 0) throw new Error('EVALUATION_TEMPLATES_EMPTY');
            if (evalTemplates.length > 1) {
                const batches = await Promise.all(evalTemplates.map(template => this.evaluate(template, inputs, options)));
                const incomplete = batches.some(batch => batch.status === 'incomplete');
                const failed = batches.some(batch => batch.status === 'failed');
                return {
                    eval_results: batches.flatMap(batch => batch.eval_results),
                    warnings: batches.flatMap(batch => batch.warnings ?? []),
                    status: incomplete ? 'incomplete' : failed ? 'failed' : 'completed',
                    reasonCode: incomplete ? 'EVALUATION_INCOMPLETE' : failed ? 'EVALUATION_FAILED' : 'EVALUATION_COMPLETED',
                };
            }
        }

        // Handle platform configuration (e.g., Langfuse)
        if (platform) {
            if (typeof evalTemplates === 'string' && typeof inputs === 'object' && customEvalName) {
                return this._configureEvaluations(
                    evalTemplates,
                    inputs,
                    platform,
                    customEvalName,
                    modelName
                );
            } else {
                throw new Error("Invalid arguments for platform configuration");
            }
        }

        let traceEval = initialTraceEval;
        let spanId: string | undefined = undefined;
        const warnings: NonNullable<BatchRunResult['warnings']> = [];
        const disableTracing = () => {
            traceEval = false;
            spanId = undefined;
            if (!warnings.includes('TRACING_UNAVAILABLE')) warnings.push('TRACING_UNAVAILABLE');
        };

        const extractName = (t: string | EvalTemplate): string | undefined => {
            if (typeof t === 'string') {
                return t;
            }
            if (t !== null && typeof t === 'object' && typeof t.eval_name === 'string') {
                return t.eval_name;
            }
            return undefined;
        };

        const firstTemplate = Array.isArray(evalTemplates) ? evalTemplates[0] : evalTemplates;
        const evalName = extractName(firstTemplate);

        if (!evalName) {
            throw new TypeError("Unsupported eval_templates argument. Expect eval template class/obj or name str.");
        }

        // Optional telemetry can fail independently; it must not skip evaluation.
        if (traceEval) {
            try {
                if (!customEvalName) throw new Error('TRACING_UNAVAILABLE');
                const otel = await import('@opentelemetry/api');
                const { checkCustomEvalConfigExists } = await import('@traceai/fi-core');
                const currentSpan = otel.trace.getSpan(otel.context.active());
                if (!currentSpan?.isRecording()) throw new Error('TRACING_UNAVAILABLE');
                const spanContext = currentSpan.spanContext();
                if (!otel.isSpanContextValid(spanContext)) throw new Error('TRACING_UNAVAILABLE');
                const provider = otel.trace.getTracerProvider() as any;
                const resource = provider.resource || (currentSpan as any).resource;
                const projectName = resource?.attributes?.project_name ?? resource?.attributes?.['service.name'];
                if (!projectName) throw new Error('TRACING_UNAVAILABLE');
                const exists = await checkCustomEvalConfigExists(projectName, [{
                    custom_eval_name: customEvalName, eval_name: evalName, mapping: {}, config: {},
                }]);
                if (exists !== false) throw new Error('TRACING_UNAVAILABLE');
                spanId = spanContext.spanId;
            } catch {
                disableTracing();
                console.warn('TRACING_UNAVAILABLE');
            }
        }

        if (Array.isArray(inputs)) {
            throw new TypeError("'inputs' must be a dictionary, array-of-dicts is not supported.");
        }

        // Validation errors reject before transport; never submit unmapped input.
        const mappedInputs = await mapInputsToBackend(evalName, inputs, {
            baseUrl: this.baseUrl, apiKey: this.fiApiKey, secretKey: this.fiSecretKey,
        });

        // The api accepts strings, list[str], or list[list[str]]. Serialize
        // dict / list-of-dicts / list-of-mixed values (e.g. conversation
        // messages) to JSON so users can pass native objects without
        // manually stringifying.
        const transformedApiInputs: Record<string, any> = {};
        for (const [key, value] of Object.entries(mappedInputs)) {
            transformedApiInputs[key] = coerceToApiInput(value);
        }

        const finalApiPayload: Record<string, any> = {
            eval_name: evalName,
            inputs: transformedApiInputs,
            model: modelName,
            span_id: spanId,
            custom_eval_name: customEvalName,
            trace_eval: traceEval,
            is_async: isAsync,
            error_localizer: errorLocalizer,
        };

        if (evalConfig) {
            finalApiPayload.config = { params: evalConfig };
        }

        const timeoutMs = timeout !== undefined ? timeout * 1000 : this.defaultTimeout * 1000;

        try {
            const response = await this.request(
                {
                    method: HttpMethod.POST,
                    url: `${this.baseUrl}/${Routes.evaluatev2}`,
                    json: finalApiPayload,
                    timeout: timeoutMs,
                },
                EvalResponseHandler
            ) as BatchRunResult;
            return completedBatch(response, warnings);
        } catch {
            return {
                eval_results: [{ name: evalName, output: null, failure: true, reason: 'EVALUATION_REQUEST_FAILED', runtime: 0 }],
                status: 'incomplete', reasonCode: 'EVALUATION_INCOMPLETE', warnings,
            };
        }
    }

    private async _get_eval_info(evalName: string): Promise<Record<string, any>> {
        if (this.evalInfoCache.has(evalName)) {
            return this.evalInfoCache.get(evalName)!;
        }

        const response = await this.requestWithFixedDiagnostics(
            {
                method: HttpMethod.GET,
                url: `${this.baseUrl}/${Routes.get_eval_templates}`,
            },
            EvalInfoResponseHandler
        ) as Record<string, any>[];

        const evalInfo = response.find(item => item.name === evalName);

        if (!evalInfo) {
            throw new Error('TEMPLATE_UNKNOWN');
        }

        this.evalInfoCache.set(evalName, evalInfo);
        return evalInfo;
    }

    public async list_evaluations(): Promise<Record<string, any>[]> {
        const config: RequestConfig = {
            method: HttpMethod.GET,
            url: `${this.baseUrl}/${Routes.get_eval_templates}`
        };
        const response = await this.requestWithFixedDiagnostics(config, EvalInfoResponseHandler) as Record<string, any>[];
        return response;
    }

    /**
     * Get the raw evaluation status payload by ID (unparsed).
     * For a higher-level handle that understands the status envelope and
     * can be awaited, use `getExecution` instead.
     */
    public async getEvalResult(evalId: string): Promise<Record<string, any>> {
        if (typeof evalId !== 'string' || !evalId.trim()) throw new Error('EVALUATION_INPUT_INVALID');
        const config: RequestConfig = {
            method: HttpMethod.GET,
            url: `${this.baseUrl}/${Routes.get_eval_result}`,
            params: { eval_id: evalId },
            timeout: this.defaultTimeout * 1000,
        };
        const response = await this.requestWithFixedDiagnostics(config);
        return responseData(response, 'EVALUATION_RESULT_INVALID');
    }

    /**
     * Evaluate a pipeline
     */
    public async evaluatePipeline(
        projectName: string,
        version: string,
        evalData: PipelineEvalData[]
    ): Promise<Record<string, any>> {
        evalData = snapshotBoundary(evalData, 'EVALUATION_INPUT_INVALID');
        if (typeof projectName !== 'string' || !projectName.trim() || typeof version !== 'string' ||
            !Array.isArray(evalData) || evalData.length === 0) throw new Error('EVALUATION_INPUT_INVALID');
        const apiPayload = {
            project_name: projectName,
            version: version,
            eval_data: evalData
        };

        const config: RequestConfig = {
            method: HttpMethod.POST,
            url: `${this.baseUrl}/${Routes.evaluate_pipeline}`,
            json: apiPayload,
            timeout: this.defaultTimeout * 1000,
        };

        const response = await this.requestWithFixedDiagnostics(config);
        return responseData(response, 'EVALUATION_RESULT_INVALID');
    }

    /**
     * Get pipeline evaluation results
     */
    public async getPipelineResults(
        projectName: string,
        versions: string[]
    ): Promise<Record<string, any>> {
        versions = snapshotBoundary(versions, 'EVALUATION_INPUT_INVALID');
        if (typeof projectName !== 'string' || !Array.isArray(versions) || !versions.every(v => typeof v === 'string')) {
            throw new TypeError("versions must be an array of strings");
        }

        const config: RequestConfig = {
            method: HttpMethod.GET,
            url: `${this.baseUrl}/${Routes.evaluate_pipeline}`,
            params: {
                project_name: projectName,
                versions: versions.join(',')
            },
            timeout: this.defaultTimeout * 1000,
        };

        const response = await this.requestWithFixedDiagnostics(config);
        return responseData(response, 'EVALUATION_RESULT_INVALID');
    }

    /**
     * Configure evaluations on a specified platform (e.g., Langfuse)
     * @private
     */
    private async _configureEvaluations(
        evalTemplates: string,
        inputs: Record<string, any>,
        platform: string,
        customEvalName: string,
        modelName?: string
    ): Promise<BatchRunResult> {
        const kwargs: Record<string, any> = {};

        if (platform === "langfuse") {
            kwargs.langfuse_secret_key = this.langfuseSecretKey;
            kwargs.langfuse_public_key = this.langfusePublicKey;
            kwargs.langfuse_host = this.langfuseHost;
        }

        try {
            const otel = await import('@opentelemetry/api');
            const currentSpan = otel.trace.getSpan(otel.context.active());

            if (currentSpan && currentSpan.isRecording()) {
                const spanContext = currentSpan.spanContext();
                if (otel.isSpanContextValid(spanContext)) {
                    kwargs.span_id = spanContext.spanId;
                    kwargs.trace_id = spanContext.traceId;
                }
            }
        } catch {
            throw new Error('PLATFORM_CONTEXT_UNAVAILABLE');
        }

        if (!kwargs.span_id || !kwargs.trace_id) throw new Error('PLATFORM_CONTEXT_UNAVAILABLE');

        const apiPayload = {
            eval_config: {
                eval_templates: evalTemplates,
                inputs: inputs,
                model_name: modelName
            },
            custom_eval_name: customEvalName,
            platform: platform,
            ...kwargs
        };

        const config: RequestConfig = {
            method: HttpMethod.POST,
            url: `${this.baseUrl}/${Routes.configure_evaluations}`,
            json: apiPayload,
            timeout: this.defaultTimeout * 1000,
        };

        let response;
        try {
            response = await this.request(config);
        } catch {
            throw new Error('PLATFORM_REQUEST_FAILED');
        }
        const batch = responseData(response, 'PLATFORM_RESULT_INVALID');
        const result = completedBatch(batch);
        if (result.status === 'incomplete') throw new Error('PLATFORM_RESULT_INVALID');
        return result;
    }

    // ------------------------------------------------------------------
    // Async submission / execution handles
    // ------------------------------------------------------------------

    /**
     * Submit an eval for async execution. Returns an `Execution` handle
     * immediately; call `handle.wait()` or `Evaluator.getExecution(id)`
     * to poll until completion.
     *
     * Internally forces `is_async=true` so the backend records the
     * evaluation and starts a worker without holding the HTTP connection.
     */
    public async submit(
        evalTemplates: string | EvalTemplate | (string | EvalTemplate)[],
        inputs: Record<string, string | string[]>,
        options: {
            timeout?: number;
            modelName?: string;
            customEvalName?: string;
            traceEval?: boolean;
            errorLocalizer?: boolean;
        } = {}
    ): Promise<Execution> {
        ({ evalTemplates, inputs, options } = snapshotBoundary({ evalTemplates, inputs, options }, 'EVALUATION_CONFIG_INVALID'));
        if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs) || !options || typeof options !== 'object') throw new Error('EVALUATION_CONFIG_INVALID');
        if (Array.isArray(evalTemplates) && evalTemplates.length !== 1) throw new Error('ASYNC_TEMPLATE_COUNT_INVALID');
        if (options.timeout !== undefined && (typeof options.timeout !== 'number' || !Number.isFinite(options.timeout) || options.timeout < 0)) throw new Error('EVALUATION_CONFIG_INVALID');
        for (const key of ['traceEval', 'errorLocalizer'] as const) if (options[key] !== undefined && typeof options[key] !== 'boolean') throw new Error('EVALUATION_CONFIG_INVALID');
        const extractName = (t: string | EvalTemplate): string | undefined => {
            if (typeof t === 'string') return t;
            if (typeof t === 'object' && t.eval_name) return t.eval_name;
            return undefined;
        };

        const firstTemplate = Array.isArray(evalTemplates)
            ? evalTemplates[0]
            : evalTemplates;
        const evalName = extractName(firstTemplate);
        if (!evalName) {
            throw new TypeError(
                "Unsupported evalTemplates argument. Expect template class/obj or name string."
            );
        }

        const transformedInputs: Record<string, string[]> = {};
        for (const [key, value] of Object.entries(inputs)) {
            transformedInputs[key] = Array.isArray(value) ? value : [value];
        }

        const payload = {
            eval_name: evalName,
            inputs: transformedInputs,
            model: options.modelName,
            span_id: undefined,
            custom_eval_name: options.customEvalName,
            trace_eval: options.traceEval ?? false,
            is_async: true,
            error_localizer: options.errorLocalizer ?? false,
        };

        const timeoutMs =
            options.timeout !== undefined
                ? options.timeout * 1000
                : (this.defaultTimeout ?? 30) * 1000;

        const raw = (await this.requestWithFixedDiagnostics({
            method: HttpMethod.POST,
            url: `${this.baseUrl}/${Routes.evaluatev2}`,
            json: payload,
            timeout: timeoutMs,
        })) as any;
        const body = responseData(raw, 'EVALUATION_RESULT_INVALID');

        if (!body || (body.status !== undefined && body.status !== true)) {
            throw new Error(
                'ASYNC_SUBMIT_REJECTED'
            );
        }
        const results = body?.result;
        if (!Array.isArray(results) || results.length !== 1) {
            throw new Error(
                'ASYNC_SUBMIT_RESULT_INVALID'
            );
        }
        const evaluations = results[0]?.evaluations ?? [];
        if (!Array.isArray(evaluations) || evaluations.length !== 1) throw new Error('ASYNC_SUBMIT_RESULT_INVALID');
        const firstEval = evaluations[0] ?? {};
        const executionId: string | undefined = firstEval.eval_id;
        if (typeof executionId !== 'string' || !executionId.trim()) {
            throw new Error(
                'ASYNC_SUBMIT_ID_MISSING'
            );
        }

        const handle = new Execution({
            id: String(executionId),
            kind: 'eval',
            status: 'pending',
        });
        handle._setRefresher(() => this._refreshEvalExecution(String(executionId)));
        return handle;
    }

    /**
     * Fetch the latest state of an async single-eval execution by id.
     * Returns an `Execution` with a refresher attached — call
     * `handle.wait()` to block until completion.
     */
    public async getExecution(executionId: string): Promise<Execution> {
        if (typeof executionId !== 'string' || !executionId.trim()) throw new Error('EVALUATION_INPUT_INVALID');
        const handle = await this._refreshEvalExecution(executionId);
        handle._setRefresher(() => this._refreshEvalExecution(executionId));
        return handle;
    }

    private async _refreshEvalExecution(executionId: string): Promise<Execution> {
        const raw = (await this.requestWithFixedDiagnostics({
            method: HttpMethod.GET,
            url: `${this.baseUrl}/${Routes.get_eval_result}`,
            params: { eval_id: executionId },
            timeout: (this.defaultTimeout ?? 30) * 1000,
        })) as any;
        const body = responseData(raw, 'EVALUATION_RESULT_INVALID');
        // Envelope: { status, result: { eval_status, result: <dict|str>, error_message? } }
        const payload = body?.result ?? {};
        const token = payload.eval_status;
        const validStatus = typeof token === 'string' && ['pending', 'PENDING', 'processing', 'PROCESSING', 'running', 'completed', 'COMPLETED', 'failed', 'FAILED'].some(value => token === value);
        let status = validStatus ? normalizeStatus(token) : 'failed' as const;
        const rawResult = payload.result;
        let parsedResult: EvalResult | null = null;
        let errorLocalizer: Record<string, any> | null = null;
        let errorMessage: string | null = null;
        if (!validStatus || (!body || (body.status !== undefined && body.status !== true)) || payload.error_message) {
            status = 'failed'; errorMessage = 'EVALUATION_RESULT_INVALID';
        }
        if (status === 'completed') {
            // The async status aliases have already been checked and normalized.
            // Other verdict fields on the enclosing payload still constrain its child.
            const parentState = terminalState({ ...payload, eval_status: status });
            const row = rawResult && typeof rawResult === 'object' && !Array.isArray(rawResult)
                ? { ...rawResult, output: 'output' in rawResult ? rawResult.output : rawResult.value } : null;
            const batch = completedBatch({ eval_results: [row] });
            if (parentState !== 'completed' || batch.status !== 'completed') {
                status = 'failed'; errorMessage = 'EVALUATION_RESULT_INVALID';
            } else {
                parsedResult = batch.eval_results[0];
                errorLocalizer = parsedResult?.error_localizer ?? null;
            }
        }
        if (status === 'failed' && !errorMessage) errorMessage = 'EVALUATION_FAILED';

        return new Execution({
            id: executionId,
            kind: 'eval',
            status,
            result: parsedResult,
            errorMessage,
            errorLocalizer,
        });
    }
}

/**
 * Convenience function to run a single or batch of evaluations.
 * @param evalTemplates - Evaluation name string (e.g., "Factual Accuracy") or list of templates.
 * @param inputs - Single test case or list of test cases as dictionaries.
 * @param options - Optional parameters for the evaluation.
 * @returns BatchRunResult containing evaluation results.
 */
export const evaluate = (
    evalTemplates: string | EvalTemplate | (string | EvalTemplate)[],
    inputs: Record<string, string | string[]>,
    options: EvaluatorConfig & EvaluateOptions = {}
): Promise<BatchRunResult> => {
    ({ evalTemplates, inputs, options } = snapshotBoundary({ evalTemplates, inputs, options }, 'EVALUATION_CONFIG_INVALID'));
    const { fiApiKey, fiSecretKey, fiBaseUrl, ...evalOptions } = options;
    return new Evaluator({ fiApiKey, fiSecretKey, fiBaseUrl }).evaluate(evalTemplates, inputs, evalOptions);
};

/**
 * Convenience function to fetch information about all available evaluation templates.
 * @returns A list of evaluation template information dictionaries.
 */
export const list_evaluations = (options: EvaluatorConfig = {}): Promise<Record<string, any>[]> => {
    options = snapshotBoundary(options, 'EVALUATION_CONFIG_INVALID');
    const { fiApiKey, fiSecretKey, fiBaseUrl } = options;
    return new Evaluator({ fiApiKey, fiSecretKey, fiBaseUrl }).list_evaluations();
};

/**
 * Convenience function to get an evaluation result by ID.
 * @param evalId - The evaluation ID.
 * @param options - Optional Evaluator configuration.
 * @returns The evaluation result.
 */
export const get_eval_result = (
    evalId: string,
    options: EvaluatorConfig = {}
): Promise<Record<string, any>> => {
    options = snapshotBoundary(options, 'EVALUATION_CONFIG_INVALID');
    const { fiApiKey, fiSecretKey, fiBaseUrl } = options;
    return new Evaluator({ fiApiKey, fiSecretKey, fiBaseUrl }).getEvalResult(evalId);
};

/**
 * Convenience function to evaluate a pipeline.
 * @param projectName - The project name.
 * @param version - The version string.
 * @param evalData - The evaluation data.
 * @param options - Optional Evaluator configuration.
 * @returns The evaluation response.
 */
export const evaluate_pipeline = (
    projectName: string,
    version: string,
    evalData: PipelineEvalData[],
    options: EvaluatorConfig = {}
): Promise<Record<string, any>> => {
    options = snapshotBoundary(options, 'EVALUATION_CONFIG_INVALID');
    const { fiApiKey, fiSecretKey, fiBaseUrl } = options;
    return new Evaluator({ fiApiKey, fiSecretKey, fiBaseUrl }).evaluatePipeline(projectName, version, evalData);
};

/**
 * Convenience function to get pipeline evaluation results.
 * @param projectName - The project name.
 * @param versions - List of versions.
 * @param options - Optional Evaluator configuration.
 * @returns The pipeline results.
 */
export const get_pipeline_results = (
    projectName: string,
    versions: string[],
    options: EvaluatorConfig = {}
): Promise<Record<string, any>> => {
    options = snapshotBoundary(options, 'EVALUATION_CONFIG_INVALID');
    const { fiApiKey, fiSecretKey, fiBaseUrl } = options;
    return new Evaluator({ fiApiKey, fiSecretKey, fiBaseUrl }).getPipelineResults(projectName, versions);
};
