import { Evaluator, EvalResponseHandler } from '../evaluator.js';
import { Protect } from '../protect.js';
import { mapInputsToBackend } from '../core/cloudRegistry.js';
import {
    DatasetNotFoundError, InvalidAuthError, RateLimitError, ServerError,
    ServiceUnavailableError, SDKException,
} from '../core/index.js';

// Offline transport and registry seam only; no backend, timers or package builds run here.
jest.mock('../core/cloudRegistry.js', () => ({ mapInputsToBackend: jest.fn(async (_name, inputs) => inputs) }));

const rules = [{ metric: 'Toxicity' }];
const pass = () => ({ output: 1, output_type: 'score', failure: false, data: ['Passed'], runtime: 0 });
// d251d8f5 lacks the optional aggregate status type; keep RED at the assertion, not compilation.
const batchStatus = (batch: unknown) => (batch as { status?: unknown }).status;
const parse = (result: unknown) => EvalResponseHandler._parseSuccess({ data: { status: true, result } } as any);
const protectBatch = (batch: unknown, flash: boolean) => new Protect({
    evaluator: { baseUrl: 'https://fixture.invalid', request: jest.fn().mockResolvedValue(batch) } as unknown as Evaluator,
}).protect('input', rules, undefined, true, 1000, flash);

let evaluator: Evaluator;
let request: jest.SpyInstance;
beforeEach(() => {
    jest.useFakeTimers();
    evaluator = new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture', fiBaseUrl: 'https://fixture.invalid' });
    request = jest.spyOn(evaluator, 'request');
});
afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
    jest.restoreAllMocks();
});

const malformedVerdicts = [
    ['nonnumeric score', { output: 'not-a-score', output_type: 'score' }],
    ['numeric-string score', { output: '1', output_type: 'score' }],
    ['null score', { output: null, output_type: 'score' }],
    ['undefined score', { output: undefined, output_type: 'score' }],
    ['infinite score', { output: Infinity, output_type: 'score' }],
    ['NaN score', { output: NaN, output_type: 'score' }],
    ['mistyped boolean', { output: 'false', output_type: 'boolean' }],
    ['mistyped text', { output: { unexpected: true }, output_type: 'text' }],
    ['mistyped tag', { output: 1, output_type: 1 }],
    ['pending output', { output: 'Pending' }],
    ['pending eval status', { eval_status: 'pending' }],
    ['processing eval status', { eval_status: 'processing' }],
    ['null eval status', { eval_status: null }],
    ['pending row status', { status: 'pending' }],
    ['malformed error state', { error_message: { unexpected: true } }],
] as const;

describe.each([false, true])('R4 response Protect route flash=%s', flash => {
    test.each(malformedVerdicts)('R4-R01 injected %s cannot pass', async (_name, fields) => {
        const result = await protectBatch({ eval_results: [{ ...pass(), ...fields }] }, flash);
        expect(batchStatus(result)).toBe('incomplete');
        expect(result.coverage.completed).toBe(0);
        expect(result.reasons).not.toBe('All checks passed');
    });

    test.each(malformedVerdicts)('R4-R02 parsed %s cannot pass', async (_name, fields) => {
        const batch = parse([{ evaluations: [{ ...pass(), ...fields }] }]);
        const result = await protectBatch(batch, flash);
        expect(batchStatus(result)).toBe('incomplete');
        expect(result.coverage.completed).toBe(0);
    });

    test.each([
        { eval_status: 'failed' }, { status: 'failed' }, { passed: false },
        { error_message: 'PRIVATE_DIAGNOSTIC' },
    ])('R4-R03 supplied terminal failure %p stays failed', async fields => {
        const result = await protectBatch({ eval_results: [{ ...pass(), ...fields }] }, flash);
        expect(batchStatus(result)).toBe('failed');
        expect(JSON.stringify(result)).not.toContain('PRIVATE_DIAGNOSTIC');
    });

    test('R4-R04 minimal legacy and genuine completed verdicts remain sufficient', async () => {
        const minimal = flash ? { failure: false } : { failure: false, data: ['Passed'] };
        expect((await protectBatch({ eval_results: [minimal] }, flash)).status).toBe('passed');
        const batch = parse([{ eval_status: 'completed', result: { evaluations: [pass()] } }]);
        expect((await protectBatch(batch, flash)).status).toBe('passed');
    });

    test('R4-R05 changing/nonenumerable verdict getters are captured once', async () => {
        const output = jest.fn().mockReturnValueOnce('1').mockReturnValue(1);
        const row = { failure: false, data: ['Passed'], output_type: 'score' };
        Object.defineProperty(row, 'output', { get: output, enumerable: false });
        expect((await protectBatch({ eval_results: [row] }, flash)).status).toBe('incomplete');
        expect(output).toHaveBeenCalledTimes(1);
    });

    test('R4-R06 strict cardinality and absolute deadline remain fail-closed', async () => {
        for (const rows of [[], [pass(), pass()], [pass(), null]]) {
            expect((await protectBatch({ eval_results: rows }, flash)).status).toBe('incomplete');
        }
        jest.setSystemTime(0);
        const late = jest.fn(async () => {
            jest.setSystemTime(1000);
            return { eval_results: [pass()] };
        });
        const client = new Protect({ evaluator: { baseUrl: 'https://fixture.invalid', request: late } as unknown as Evaluator });
        expect((await client.protect('input', rules, undefined, true, 1000, flash)).status).toBe('incomplete');
        expect(late).toHaveBeenCalledTimes(1);
    });
});

describe('R4 enclosing response states', () => {
    test.each([
        { eval_status: 'failed' }, { status: 'failed' }, { failure: true },
        { passed: false }, { error_message: 'PRIVATE_DIAGNOSTIC' },
    ])('R4-R07 failed nested envelope %p cannot be erased by a child', async fields => {
        const batch = parse([{ eval_status: 'completed', result: { ...fields, evaluations: [pass()] } }]);
        request.mockResolvedValue(batch);
        const result = await evaluator.evaluate('fixture', { output: 'answer' });
        expect(batchStatus(result)).toBe('failed');
        expect(result.eval_results).toHaveLength(1);
        expect(result.eval_results[0]).toMatchObject({ output: 1, failure: true });
        expect(JSON.stringify(result)).not.toContain('PRIVATE_DIAGNOSTIC');
        for (const flash of [false, true]) expect((await protectBatch(batch, flash)).status).toBe('failed');
    });

    test.each([
        { eval_status: 'pending' }, { status: 'processing' }, { failure: 'false' },
        { passed: 'yes' }, { error_message: {} },
    ])('R4-R08 incomplete nested envelope %p invalidates every child', async fields => {
        request.mockResolvedValue(parse([{ eval_status: 'completed', result: { ...fields, evaluations: [pass(), pass()] } }]));
        const result = await evaluator.evaluate('fixture', { output: 'answer' });
        expect(batchStatus(result)).toBe('incomplete');
        expect(result.eval_results).toHaveLength(2);
        expect(result.eval_results.every(row => row?.failure === true && row.output === null)).toBe(true);
    });

    test.each([
        ['empty', [], 1], ['null group', null, 1], ['null child', [null], 1],
        ['mixed children', [pass(), null], 2],
    ])('R4-R09 %s retains invalid slots inside a failed envelope', async (_name, children, count) => {
        request.mockResolvedValue(parse([{ eval_status: 'completed', result: { failure: true, evaluations: children } }]));
        const result = await evaluator.evaluate('fixture', { output: 'answer' });
        expect(batchStatus(result)).toBe('incomplete');
        expect(result.eval_results).toHaveLength(count as number);
        expect(result.eval_results.every(row => row?.failure === true)).toBe(true);
    });

    test('R4-R10 direct grouping envelopes also retain failure', async () => {
        request.mockResolvedValue(parse([{ failure: true, evaluations: [pass()] }]));
        const result = await evaluator.evaluate('fixture', { output: 'answer' });
        expect(batchStatus(result)).toBe('failed');
        expect(result.eval_results[0]).toMatchObject({ output: 1, failure: true });
    });

    test('R4-R11 explicit null output cannot fall back to an unrelated value', async () => {
        request.mockResolvedValue(parse([{ evaluations: [{ ...pass(), output: null, value: 1 }] }]));
        expect(batchStatus(await evaluator.evaluate('fixture', { output: 'answer' }))).toBe('incomplete');
    });

    test('R4-R12 pending acknowledgement retains its ID but never completes', async () => {
        const batch = parse([{ evaluations: [{ output: 'Pending', eval_id: 'fixture-id' }] }]);
        expect(batchStatus(batch)).toBe('incomplete');
        expect(batch.eval_results[0]).toMatchObject({ output: 'Pending', eval_id: 'fixture-id' });
        request.mockResolvedValue(batch);
        expect(batchStatus(await evaluator.evaluate('fixture', { output: 'answer' }))).toBe('incomplete');
    });
});

describe('R4 declared text completion', () => {
    test.each([
        { failure: true }, { passed: false }, { status: 'failed' },
        { status: 'pending' }, { failure: 'false' }, { error_message: {} },
    ])('R4-R22 async completed parent %p cannot override contradictory evidence', async fields => {
        request.mockResolvedValue({ data: { status: true, result: {
            eval_status: 'completed', ...fields, result: pass(),
        } } });
        const result = await evaluator.getExecution('fixture-id');
        expect(batchStatus(result)).toBe('failed');
        expect(result.result).toBeNull();
        expect(result.errorMessage).toBe('EVALUATION_RESULT_INVALID');
    });

    test('R4-R23 async supplied undefined output cannot use the value alias', async () => {
        request.mockResolvedValue({ data: { status: true, result: {
            eval_status: 'completed', result: { output: undefined, value: 1, output_type: 'score' },
        } } });
        expect((await evaluator.getExecution('fixture-id')).status).toBe('failed');
    });

    test.each([{}, [], 1, true, null, undefined])('R4-R13 sync non-text %p stays incomplete', async output => {
        request.mockResolvedValue(parse([{ evaluations: [{ output, output_type: 'text', runtime: 0 }] }]));
        const result = await evaluator.evaluate('fixture', { output: 'answer' });
        expect(batchStatus(result)).toBe('incomplete');
        expect(result.eval_results[0]?.failure).toBe(true);
    });

    test.each([{}, [], 1, true, null, undefined])('R4-R14 async non-text %p stays failed', async output => {
        request.mockResolvedValue({ data: { status: true, result: {
            eval_status: 'completed', result: { output, output_type: 'text', runtime: 0 },
        } } });
        const result = await evaluator.getExecution('fixture-id');
        expect(batchStatus(result)).toBe('failed');
        expect(result.result).toBeNull();
        expect(result.errorMessage).toBe('EVALUATION_RESULT_INVALID');
    });

    test.each([
        { output: '', output_type: 'text' }, { output: 'PAYLOAD_TEXT', output_type: 'text' },
        { output: 1, output_type: 'score' }, { output: false, output_type: 'boolean' },
        { output: 'pass', output_type: 'Pass/Fail' }, { output: { valid: true }, output_type: 'json' },
        { output: { valid: true } },
    ])('R4-R15 genuine declared/legacy/structured output %p is preserved', async row => {
        const payload = { ...row, runtime: 0, data: ['PAYLOAD_DATA'], error_localizer: { text: 'PAYLOAD_LOCALIZER' } };
        request.mockResolvedValue(parse([{ eval_status: 'completed', result: { evaluations: [payload] } }]));
        const sync = await evaluator.evaluate('fixture', { output: 'answer' });
        expect(batchStatus(sync)).toBe('completed');
        expect(sync.eval_results).toHaveLength(1);
        expect(sync.eval_results[0]).toMatchObject({ ...payload, failure: false });
        request.mockResolvedValue({ data: { status: true, result: { eval_status: 'completed', result: payload } } });
        const asyncResult = await evaluator.getExecution('fixture-id');
        expect(asyncResult.status).toBe('completed');
        expect(asyncResult.result).toMatchObject({ ...payload, failure: false });
        expect(asyncResult.errorLocalizer).toEqual(payload.error_localizer);
    });

    test('R4-R16 parser validates the first captured child state, not a second getter value', async () => {
        const status = jest.fn().mockReturnValueOnce('pending').mockReturnValue('completed');
        const row = pass();
        Object.defineProperty(row, 'eval_status', { get: status, enumerable: false });
        request.mockResolvedValue(parse([{ evaluations: [row] }]));
        expect(batchStatus(await evaluator.evaluate('fixture', { output: 'answer' }))).toBe('incomplete');
        expect(status).toHaveBeenCalledTimes(1);
    });
});

const rawCalls: [string, (client: Evaluator) => Promise<unknown>][] = [
    ['getExecution', client => client.getExecution('fixture-id')],
    ['submit', client => client.submit('fixture', { output: 'answer' })],
    ['getEvalResult', client => client.getEvalResult('fixture-id')],
    ['evaluatePipeline', client => client.evaluatePipeline('fixture', 'v1', [{ eval_name: 'fixture' }] as any)],
    ['getPipelineResults', client => client.getPipelineResults('fixture', ['v1'])],
    ['list_evaluations', client => client.list_evaluations()],
];

describe('R4 owned transport diagnostics', () => {
    test.each(rawCalls)('R4-R17 %s rejection uses only a fixed diagnostic', async (_name, call) => {
        request.mockRejectedValue(new Error('PRIVATE_DIAGNOSTIC'));
        const error = await call(evaluator).then(() => null, error => error);
        expect(error).toBeInstanceOf(Error);
        expect(error.message).toBe('EVALUATION_REQUEST_FAILED');
        expect(JSON.stringify(error)).not.toContain('PRIVATE_DIAGNOSTIC');
        expect(error.cause).toBeUndefined();
        expect(request).toHaveBeenCalledTimes(1);
    });

    test.each([InvalidAuthError, DatasetNotFoundError, RateLimitError, ServerError, ServiceUnavailableError, SDKException])(
        'R4-R18 transport exception family %p survives without its diagnostic/cause', async ErrorType => {
            request.mockRejectedValue(new ErrorType('PRIVATE_DIAGNOSTIC', new Error('PRIVATE_CAUSE')));
            const error = await evaluator.getExecution('fixture-id').then(() => null, error => error);
            expect(error).toBeInstanceOf(ErrorType);
            expect(error.getMessage()).toBe('EVALUATION_REQUEST_FAILED');
            expect(error.cause).toBeUndefined();
            expect(JSON.stringify(error)).not.toContain('PRIVATE_');
        }
    );

    test('R4-R24 hostile rejected transport values cannot leak during error classification', async () => {
        const rejected = new Proxy({}, { getPrototypeOf() { throw new Error('PRIVATE_DIAGNOSTIC'); } });
        request.mockRejectedValue(rejected);
        const error = await evaluator.getExecution('fixture-id').then(() => null, error => error);
        expect(error === rejected).toBe(false);
        expect(error.message).toBe('EVALUATION_REQUEST_FAILED');
    });

    test('R4-R19 submitted handle refresh inherits the fixed transport boundary', async () => {
        request.mockResolvedValueOnce({ data: { status: true, result: [{ evaluations: [{ eval_id: 'fixture-id' }] }] } });
        const handle = await evaluator.submit('fixture', { output: 'answer' });
        expect(handle.status).toBe('pending');
        request.mockRejectedValue(new Error('PRIVATE_DIAGNOSTIC'));
        await expect(handle.refresh()).rejects.toThrow('EVALUATION_REQUEST_FAILED');
        expect(handle.status).toBe('pending');
    });

    test('R4-R20 captured raw application payloads remain unchanged', async () => {
        const payload = { result: { output: 'PAYLOAD_TEXT', data: ['PAYLOAD_DATA'], error_localizer: { text: 'PAYLOAD_LOCALIZER' } } };
        request.mockResolvedValue({ data: payload });
        expect(await evaluator.getEvalResult('fixture-id')).toEqual(payload);
        expect(await evaluator.evaluatePipeline('fixture', 'v1', [{ eval_name: 'fixture' }] as any)).toEqual(payload);
        expect(await evaluator.getPipelineResults('fixture', ['v1'])).toEqual(payload);
    });

    test('R4-R21 nontransport validation and mapping errors are not indiscriminately sanitized', async () => {
        await expect(evaluator.getPipelineResults('fixture', true as any)).rejects.toBeInstanceOf(TypeError);
        const callerError = new TypeError('APPLICATION_MAPPING_ERROR');
        (mapInputsToBackend as jest.Mock).mockRejectedValueOnce(callerError);
        await expect(evaluator.evaluate('fixture', { output: 'answer' })).rejects.toBe(callerError);
        expect(request).not.toHaveBeenCalled();
    });
});
