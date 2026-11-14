import { Evaluator, EvalResponseHandler } from '../evaluator.js';
import { mapInputsToBackend } from '../core/cloudRegistry.js';
import * as otel from '@opentelemetry/api';

jest.mock('../core/cloudRegistry.js', () => ({ mapInputsToBackend: jest.fn(async (_name, inputs) => inputs) }));
jest.mock('@opentelemetry/api', () => ({
    context: { active: jest.fn() },
    trace: { getSpan: jest.fn(), getTracerProvider: jest.fn(() => ({ resource: { attributes: { project_name: 'fixture' } } })) },
    isSpanContextValid: jest.fn(() => true),
}), { virtual: true });
jest.mock('@traceai/fi-core', () => ({ checkCustomEvalConfigExists: jest.fn(async () => false) }), { virtual: true });

let evaluator: Evaluator;
let request: jest.SpyInstance;
beforeEach(() => {
    evaluator = new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture', fiBaseUrl: 'https://fixture.invalid' });
    request = jest.spyOn(evaluator, 'request').mockResolvedValue({ eval_results: [{ name: 'fixture', output: 1, reason: '', runtime: 0 }] });
    (otel.trace.getSpan as jest.Mock).mockReset().mockReturnValue(undefined);
});
afterEach(() => jest.restoreAllMocks());

describe('FO-08 evaluation versus optional telemetry', () => {
    test('optional tracing failure is observable and still performs real evaluation', async () => {
        const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
        (otel.trace.getSpan as jest.Mock).mockImplementation(() => { throw new Error('PRIVATE_SENTINEL'); });
        const result = await evaluator.evaluate('fixture', { output: 'x' }, { traceEval: true, customEvalName: 'fixture' });
        expect(request).toHaveBeenCalledTimes(1);
        expect(result.eval_results[0]?.output).toBe(1);
        expect((result as any).warnings).toContain('TRACING_UNAVAILABLE');
        expect(JSON.stringify(warn.mock.calls)).not.toContain('PRIVATE_SENTINEL');
    });
    test('platform missing span is a rejection, not an empty success batch', async () => {
        await expect(evaluator.evaluate('fixture', { output: 'x' }, { platform: 'langfuse', customEvalName: 'fixture' })).rejects.toThrow('PLATFORM_CONTEXT_UNAVAILABLE');
        expect(request).not.toHaveBeenCalled();
    });
    test('platform API failure is explicit and redacted', async () => {
        (otel.trace.getSpan as jest.Mock).mockReturnValue({ isRecording: () => true, spanContext: () => ({ spanId: 'fixture', traceId: 'fixture' }) });
        request.mockRejectedValue(new Error('PRIVATE_SENTINEL'));
        await expect(evaluator.evaluate('fixture', { output: 'x' }, { platform: 'langfuse', customEvalName: 'fixture' })).rejects.toThrow('PLATFORM_REQUEST_FAILED');
    });
    test('platform genuine nonempty result control', async () => {
        (otel.trace.getSpan as jest.Mock).mockReturnValue({ isRecording: () => true, spanContext: () => ({ spanId: 'fixture', traceId: 'fixture' }) });
        request.mockResolvedValue({ data: { eval_results: [{ output: 1, runtime: 0 }] } });
        const result = await evaluator.evaluate('fixture', { output: 'x' }, { platform: 'langfuse', customEvalName: 'fixture' });
        expect(result.eval_results[0]?.output).toBe(1);
        expect(request).toHaveBeenCalledTimes(1);
    });
    test('backend failure remains distinct from input validation failure', async () => {
        request.mockRejectedValue(new Error('PRIVATE_SENTINEL'));
        const result = await evaluator.evaluate('fixture', { output: 'x' });
        expect(result.eval_results[0]?.output).toBeNull();
        expect(result.eval_results[0]?.reason).toBe('EVALUATION_REQUEST_FAILED');
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
        expect(mapInputsToBackend).toHaveBeenCalled();
    });
    test('response parser rejects malformed verdicts and redacts backend diagnostics', () => {
        const result = EvalResponseHandler._parseSuccess({ data: { result: [{ evaluations: [{ output: 1, passed: 'yes', reason: 'PRIVATE_SENTINEL' }] }] } } as any);
        expect(result.eval_results[0]?.output).toBeNull();
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
        expect(() => EvalResponseHandler._handleError({ status: 400, data: 'PRIVATE_SENTINEL' } as any)).toThrow('EVALUATION_BAD_REQUEST');
    });
    test('empty backend batch is explicit failure', async () => {
        request.mockResolvedValue({ eval_results: [] });
        const result = await evaluator.evaluate('fixture', { output: 'x' });
        expect(result.eval_results).toHaveLength(1);
        expect(result.eval_results[0]?.output).toBeNull();
    });
    test('every requested template is evaluated rather than only the first', async () => {
        const result = await evaluator.evaluate(['fixture', 'second'], { output: 'x' });
        expect(request).toHaveBeenCalledTimes(2);
        expect(result.eval_results).toHaveLength(2);
        expect(request.mock.calls.map(call => call[0].json.eval_name)).toEqual(['fixture', 'second']);
    });
    test('R2-E01 aggregate ERROR uses a fixed diagnostic while payload fields remain payloads', async () => {
        request.mockResolvedValue({ status: 'error', reason: 'PRIVATE_DIAGNOSTIC', eval_results: [{ output: 'PAYLOAD', data: ['PAYLOAD'], error_localizer: { text: 'PAYLOAD' }, failure: true, reason: 'PRIVATE_DIAGNOSTIC', runtime: 0 }] });
        const result = await evaluator.evaluate(['fixture', 'second'], { output: 'x' });
        expect((result as any).status).toBe('incomplete');
        expect((result as any).reasonCode).toBe('EVALUATION_INCOMPLETE');
        expect(JSON.stringify(result)).not.toContain('PRIVATE_DIAGNOSTIC');
        expect(result.eval_results[0]?.output).toBe('PAYLOAD');
        expect(result.eval_results[0]?.data).toEqual(['PAYLOAD']);
        expect(result.eval_results[0]?.error_localizer).toEqual({ text: 'PAYLOAD' });
    });
    test('R2-E02 boxed backend status cannot claim completion', async () => {
        request.mockResolvedValue({ status: new String('completed'), eval_results: [{ output: 1, runtime: 0 }] });
        expect((await evaluator.evaluate('fixture', { output: 'x' }) as any).status).toBe('incomplete');
    });
    test('R2-E04 typed scores and flags must be primitive and finite', async () => {
        for (const row of [{ output: '1', output_type: 'score' }, { output: new Number(1), output_type: 'score' }, { output: 1, passed: new Boolean(true) }, { output: 1, output_type: new String('score') }]) {
            request.mockResolvedValue({ eval_results: [{ ...row, runtime: 0 }] });
            expect((await evaluator.evaluate('fixture', { output: 'x' }) as any).status).toBe('incomplete');
        }
    });
    test('R2-E03 configuration acknowledgement is not an evaluation result', async () => {
        (otel.trace.getSpan as jest.Mock).mockReturnValue({ isRecording: () => true, spanContext: () => ({ spanId: 'fixture', traceId: 'fixture' }) });
        request.mockResolvedValue({ data: { status: true } });
        await expect(evaluator.evaluate('fixture', { output: 'x' }, { platform: 'langfuse', customEvalName: 'fixture' })).rejects.toThrow('PLATFORM_RESULT_INVALID');
    });
    test('genuine evaluation PASS control', async () => {
        expect((await evaluator.evaluate('fixture', { output: 'x' })).eval_results[0]?.output).toBe(1);
        expect(request).toHaveBeenCalledTimes(1);
    });
});
