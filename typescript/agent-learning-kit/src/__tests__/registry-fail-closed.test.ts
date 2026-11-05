import axios from 'axios';
import { __clearRegistryCache, loadRegistry, mapInputsToBackend, mapInputsToBackendSync } from '../core/cloudRegistry.js';
import { Evaluator } from '../evaluator.js';

jest.mock('axios');
const get = axios.get as jest.Mock;
const opts = { baseUrl: 'https://fixture.invalid', apiKey: 'fixture-key', secretKey: 'fixture-secret' };
const registry = [{ name: 'fixture', config: { required_keys: ['output'] } }];
let previousSwitch: string | undefined;
beforeEach(() => {
    previousSwitch = process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    delete process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    __clearRegistryCache();
    get.mockReset().mockResolvedValue({ data: { result: registry } });
});
afterEach(() => {
    if (previousSwitch === undefined) delete process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    else process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY = previousSwitch;
    jest.restoreAllMocks();
});

describe('W04 registry fail-closed', () => {
    test('FO-06 transport/loader failure rejects without leaking', async () => {
        const log = jest.spyOn(console, 'warn').mockImplementation(() => {});
        get.mockRejectedValue(new Error('PRIVATE_SENTINEL'));
        await expect(mapInputsToBackend('fixture', { output: 'x' }, opts)).rejects.toThrow('REGISTRY_UNAVAILABLE');
        expect(JSON.stringify(log.mock.calls)).not.toContain('PRIVATE_SENTINEL');
    });
    test.each([null, {}, { result: {} }, { result: [] }, { result: [{ name: 'fixture' }] }])('FO-06 malformed registry %p rejects', async data => {
        get.mockResolvedValue({ data });
        await expect(loadRegistry(opts)).rejects.toThrow('REGISTRY_INVALID');
    });
    test('FO-06 unknown name rejects', async () => {
        await expect(mapInputsToBackend('unknown', { output: 'x' }, opts)).rejects.toThrow('TEMPLATE_UNKNOWN');
    });
    test('FO-06 failed alias resolution rejects', async () => {
        await expect(mapInputsToBackend('fixture', { unrelated: 'x' }, opts)).rejects.toThrow('REQUIRED_INPUT_MISSING');
    });
    test('FO-06 synchronous cache miss rejects', () => {
        expect(() => mapInputsToBackendSync('fixture', { output: 'x' }, opts.baseUrl, opts.apiKey)).toThrow('REGISTRY_NOT_LOADED');
    });
    test('FO-06 complete credential identity isolates registry caches', async () => {
        await loadRegistry({ ...opts, apiKey: 'same-prefix-first' });
        await loadRegistry({ ...opts, apiKey: 'same-prefix-second' });
        await loadRegistry({ ...opts, apiKey: 'same-prefix-second', secretKey: 'other-fixture-secret' });
        expect(get).toHaveBeenCalledTimes(3);
    });
    test('FO-07 disable switch is explicit rejection, including warm cache', async () => {
        await loadRegistry(opts);
        process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY = '1';
        await expect(mapInputsToBackend('fixture', { output: 'x' }, opts)).rejects.toThrow('REGISTRY_DISABLED');
        expect(() => mapInputsToBackendSync('fixture', { output: 'x' }, opts.baseUrl, opts.apiKey)).toThrow('REGISTRY_DISABLED');
    });
    test('FO-06 evaluator cannot submit unmapped data after registry failure', async () => {
        get.mockRejectedValue(new Error('PRIVATE_SENTINEL'));
        jest.spyOn(console, 'warn').mockImplementation(() => {});
        const evaluator = new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture', fiBaseUrl: opts.baseUrl });
        const request = jest.spyOn(evaluator, 'request').mockResolvedValue({ eval_results: [{ output: 1 }] });
        await expect(evaluator.evaluate('fixture', { output: 'x' })).rejects.toThrow('REGISTRY_UNAVAILABLE');
        expect(request).not.toHaveBeenCalled();
    });
    test('R2-R01 boxed template names reject rather than coerce cache keys', async () => {
        await expect(mapInputsToBackend(new String('fixture') as any, { output: 'x' }, opts)).rejects.toThrow('TEMPLATE_UNKNOWN');
    });
    test('R2-R02 required-key iterator is snapshotted once and reused from cache', async () => {
        const keys = (function* () { yield 'output'; })();
        get.mockResolvedValue({ data: { result: [{ name: 'fixture', config: { required_keys: keys } }] } });
        await expect(mapInputsToBackend('fixture', { response: 'x' }, opts)).resolves.toEqual({ output: 'x' });
        await expect(mapInputsToBackend('fixture', { response: 'y' }, opts)).resolves.toEqual({ output: 'y' });
        expect(get).toHaveBeenCalledTimes(1);
    });
    test('R2-R03 authenticated cache cannot be borrowed by secretless synchronous mapping', async () => {
        await loadRegistry(opts);
        expect(() => mapInputsToBackendSync('fixture', { output: 'x' }, opts.baseUrl, opts.apiKey)).toThrow('REGISTRY_NOT_LOADED');
    });
    test('FO-06 genuine mapped PASS control and secretless synchronous cache', async () => {
        expect(await mapInputsToBackend('fixture', { response: 'x' }, opts)).toEqual({ output: 'x' });
        const publicOpts = { baseUrl: opts.baseUrl, apiKey: 'public-fixture' };
        await loadRegistry(publicOpts);
        expect(mapInputsToBackendSync('fixture', { response: 'x' }, publicOpts.baseUrl, publicOpts.apiKey)).toEqual({ output: 'x' });
    });
});
