import { Evaluator } from '../evaluator.js';

jest.mock('../core/cloudRegistry.js', () => ({ mapInputsToBackend: jest.fn(async (_name, inputs) => inputs) }));
// Synthetic optional-loader failure; no real peer installation or network.
jest.mock('@opentelemetry/api', () => { throw new Error('PRIVATE_SENTINEL'); }, { virtual: true });

afterEach(() => jest.restoreAllMocks());

describe('FO-08 optional import failure', () => {
    test('still evaluates and carries an observable warning', async () => {
        const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
        const evaluator = new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture', fiBaseUrl: 'https://fixture.invalid' });
        const request = jest.spyOn(evaluator, 'request').mockResolvedValue({ eval_results: [{ output: 1, runtime: 0 }] });
        const result = await evaluator.evaluate('fixture', { output: 'x' }, { traceEval: true, customEvalName: 'fixture' });
        expect(result.eval_results[0]?.output).toBe(1);
        expect(request).toHaveBeenCalledTimes(1);
        expect((result as any).warnings).toContain('TRACING_UNAVAILABLE');
        expect(JSON.stringify(warn.mock.calls)).not.toContain('PRIVATE_SENTINEL');
    });
    test('platform configuration cannot resolve empty on import failure', async () => {
        const evaluator = new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture', fiBaseUrl: 'https://fixture.invalid' });
        const request = jest.spyOn(evaluator, 'request').mockResolvedValue({ data: {} });
        await expect(evaluator.evaluate('fixture', { output: 'x' }, { platform: 'langfuse', customEvalName: 'fixture' })).rejects.toThrow('PLATFORM_CONTEXT_UNAVAILABLE');
        expect(request).not.toHaveBeenCalled();
    });
});
