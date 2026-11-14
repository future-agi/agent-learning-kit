import axios from 'axios';
import { Evaluator, EvalResponseHandler } from '../evaluator.js';
import { Protect } from '../protect.js';
import { __clearRegistryCache, loadRegistry, mapInputsToBackend, mapInputsToBackendSync } from '../core/cloudRegistry.js';

jest.mock('axios');
const get = axios.get as jest.Mock;
const opts = { baseUrl: 'https://fixture.invalid', apiKey: 'fixture-a', secretKey: 'fixture-secret' };
let previousSwitch: string | undefined;
function evaluator() { return new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture', fiBaseUrl: opts.baseUrl }); }
beforeEach(() => {
    previousSwitch = process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    delete process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    __clearRegistryCache();
    get.mockReset().mockResolvedValue({ data: { result: [{ name: 'fixture', config: { required_keys: ['output'] } }] } });
});
afterEach(() => {
    if (previousSwitch === undefined) delete process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    else process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY = previousSwitch;
    jest.restoreAllMocks();
});

describe('W04 round3 cloud fail-closed', () => {
    test('R3-S05 registry transport and cache use identical credential snapshots', async () => {
        let reads = 0;
        await expect(loadRegistry({ ...opts, get apiKey() { return ++reads === 1 ? 'fixture-a' : 'fixture-b'; } })).resolves.toBeDefined();
        expect(reads).toBe(1);
        expect(get.mock.calls[0][1].headers['X-Api-Key']).toBe('fixture-a');
        await loadRegistry(opts); expect(get).toHaveBeenCalledTimes(1);
    });
    test('R3-S07 required input captured once, throwing getters sanitized', async () => {
        let reads = 0;
        await expect(mapInputsToBackend('fixture', { get output() { return ++reads <= 2 ? 'answer' : undefined; } }, opts)).resolves.toEqual({ output: 'answer' });
        expect(reads).toBe(1);
        await expect(mapInputsToBackend('fixture', { get output() { throw new Error('PRIVATE_SENTINEL'); } }, opts)).rejects.toThrow('REQUIRED_INPUT_MISSING');
        const publicOpts = { baseUrl: opts.baseUrl, apiKey: 'public-fixture' };
        await loadRegistry(publicOpts);
        expect(() => mapInputsToBackendSync('fixture', { get output() { throw new Error('PRIVATE_SENTINEL'); } }, publicOpts.baseUrl, publicOpts.apiKey)).toThrow('REQUIRED_INPUT_MISSING');
    });
    test('R3-B03 registry metadata fields and evaluator inputs are owned', async () => {
        let reads = 0;
        get.mockResolvedValue({ data: { result: [{ get name() { if (++reads > 1) throw new Error('PRIVATE_SENTINEL'); return 'fixture'; }, config: { required_keys: ['output'] } }] } });
        await expect(loadRegistry(opts)).resolves.toHaveProperty('fixture');
        expect(reads).toBe(1);
        get.mockResolvedValue({ data: { result: [{ name: 'fixture', config: { required_keys: ['output'] } }] } });
        const client = evaluator();
        const request = jest.spyOn(client, 'request').mockResolvedValue({ eval_results: [{ output: 1, runtime: 0 }] });
        let inputReads = 0;
        await expect(client.evaluate('fixture', { get output() { if (++inputReads > 1) throw new Error('PRIVATE_SENTINEL'); return 'answer'; } })).resolves.toMatchObject({ status: 'completed' });
        expect(inputReads).toBe(1); expect(request).toHaveBeenCalledTimes(1);
    });
    test.each([false, true])('R3-L05 both protect routes reject extra slots (flash=%p)', async flash => {
        const request = jest.fn().mockResolvedValue({ eval_results: [{ data: ['Passed'], failure: false }, { data: ['Failed'], failure: true }] });
        const client = new Protect({ evaluator: { request, baseUrl: opts.baseUrl } as unknown as Evaluator });
        expect((await client.protect('input', [{ metric: 'Toxicity' }], undefined, true, 1000, flash)).status).not.toBe('passed');
        request.mockResolvedValue({ eval_results: [{ data: ['Passed'], failure: false }, null] });
        expect((await client.protect('input', [{ metric: 'Toxicity' }], undefined, true, 1000, flash)).status).not.toBe('passed');
        request.mockResolvedValue({ eval_results: [{ data: ['Passed'], failure: false }] });
        expect((await client.protect('input', [{ metric: 'Toxicity' }], undefined, true, 1000, flash)).status).toBe('passed');
    });
    test.each([false, true])('R3-S06 protect verdict cannot change after validation (flash=%p)', async flash => {
        let reads = 0;
        const request = jest.fn().mockResolvedValue({ eval_results: [{ data: ['Passed'], get failure() { return ++reads === 1; } }] });
        const client = new Protect({ evaluator: { request, baseUrl: opts.baseUrl } as unknown as Evaluator });
        expect((await client.protect('input', [{ metric: 'Toxicity' }], undefined, true, 1000, flash)).status).toBe('failed');
        expect(reads).toBe(1);
    });
    test.each([false, true])('R3-L10 successful transport still checks absolute deadline (flash=%p)', async flash => {
        let clock = 0;
        jest.spyOn(Date, 'now').mockImplementation(() => clock);
        const request = jest.fn().mockImplementation(() => { clock = 100; return Promise.resolve({ eval_results: [{ data: ['Passed'], failure: false }] }); });
        const client = new Protect({ evaluator: { request, baseUrl: opts.baseUrl } as unknown as Evaluator });
        expect((await client.protect('input', [{ metric: 'Toxicity' }], undefined, true, 10, flash)).status).not.toBe('passed');
    });
    test('R3-C02 real response parser retains null and pending outer results', async () => {
        const client = evaluator();
        const request = jest.spyOn(client, 'request');
        for (const outer of [
            [{ evaluations: [{ output: 1, runtime: 0 }] }, null],
            [{ evaluations: [{ output: 'Pending', output_type: '', eval_id: 'fixture', runtime: 0 }] }],
            [{ eval_status: 'pending', result: { output: 1, runtime: 0 } }],
        ]) {
            request.mockImplementation(async () => EvalResponseHandler._parseSuccess({ data: { status: true, result: outer } } as any));
            await expect(client.evaluate('fixture', { output: 'answer' }, { isAsync: true })).resolves.toMatchObject({ status: 'incomplete' });
        }
        request.mockImplementation(async () => EvalResponseHandler._parseSuccess({ data: { status: true, result: [{ evaluations: [{ output: 1, runtime: 0 }] }] } } as any));
        await expect(client.evaluate('fixture', { output: 'answer' })).resolves.toMatchObject({ status: 'completed' });
        request.mockImplementation(async () => EvalResponseHandler._parseSuccess({ data: { status: true, result: [{ eval_status: 'failed', result: { output: 1, runtime: 0 } }] } } as any));
        await expect(client.evaluate('fixture', { output: 'answer' })).resolves.toMatchObject({ status: 'failed' });
    });
    test('R3-L06 terminal async completed requires valid evidence', async () => {
        const client = evaluator(); const request = jest.spyOn(client, 'request');
        for (const result of [undefined, { output: Infinity }, { output: 1, failure: 'false' }, { output: 1, passed: 'true' }]) {
            request.mockResolvedValue({ data: { status: true, result: { eval_status: 'completed', result } } });
            const handle = await client.getExecution('fixture-id');
            expect(handle.status).toBe('failed');
        }
        request.mockResolvedValue({ data: { status: true, result: { eval_status: 'completed', result: { output: 1, runtime: 0 } } } });
        expect((await client.getExecution('fixture-id')).status).toBe('completed');
    });
});
