/**
 * ALK-R5-LOCAL-EVALUATOR: offline public-route boundary regressions.
 * Authored only; execution belongs to the parent's admitted test venue.
 */
import { HybridEvaluator, LocalEvaluator } from '../evaluator';
import type { LocalEvaluationResult } from '../evaluator';
import { ExecutionMode } from '../execution-mode';
import type { Evaluator } from '../../evaluator';
import type { OllamaLLM } from '../llm';

type Evaluate = (
    metric: string,
    inputs: Array<Record<string, any>>,
    config?: Record<string, any>
) => LocalEvaluationResult | Promise<LocalEvaluationResult>;

const routes: Array<[string, Evaluate]> = [
    ['local', (metric, inputs, config) => new LocalEvaluator().evaluate(metric, inputs, config)],
    ['hybrid heuristic', (metric, inputs, config) =>
        new HybridEvaluator({ offlineMode: true }).evaluate(metric, inputs, config)],
    ['local batch', (metric, inputs, config) =>
        new LocalEvaluator().evaluateBatch([{ metricName: metric, inputs, config }])],
    ['hybrid local partition', (metric, inputs, config) =>
        new HybridEvaluator({ offlineMode: true }).evaluateLocalPartition([
            { metricName: metric, inputs, config }
        ])]
];

const unsupported = 'Copper conducts electricity.';
const supported = 'Paris is the capital of France.';
const benignCode = { response: 'const value = 1;', code: 'const value = 1;' };
const invalidConfigs: Array<[string, unknown]> = [
    ['array', []], ['null', null], ['false', false], ['zero', 0],
    ['empty string', ''], ['string', 'invalid'], ['function', () => ({})]
];

function expectBoundaryFailure(result: LocalEvaluationResult): void {
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]?.error).toBe('METRIC_CONFIG_INVALID');
    expect(result.results.eval_results).toEqual([]);
    expect(result.executedLocally).toEqual([]);
    expect(result.executedCloud).toEqual([]);
}

describe.each(routes)('%s captured evaluation', (_name, evaluate) => {
    it('R5-LE01 evaluates the first response getter value, not the later spread value', async () => {
        let reads = 0;
        const input = {
            query: 'What is the capital of France?',
            get response() { return ++reads === 1 ? unsupported : supported; },
            context: supported
        };
        const result = await evaluate('faithfulness', [input]);
        expect(reads).toBe(1);
        expect(result.errors).toEqual([]);
        expect(result.results.eval_results).toHaveLength(1);
        expect(result.results.eval_results[0]?.output).toBe(0);
    });

    it.each(invalidConfigs)('R5-LE02 rejects a supplied %s config before dispatch', async (_label, config) => {
        const result = await evaluate('secrets_detection', [benignCode], config as Record<string, any>);
        expectBoundaryFailure(result);
    });

    it('R5-LE03 captures config getters once for all input rows', async () => {
        let reads = 0;
        const config = { get keyword() { return ++reads === 1 ? 'world' : 'absent'; } };
        const result = await evaluate('contains', [{ response: 'Hello world' }, { response: 'Goodbye world' }], config);
        expect(reads).toBe(1);
        expect(result.errors).toEqual([]);
        expect(result.results.eval_results.map(row => row?.output)).toEqual([1, 1]);
    });

    it('R5-LE04 captures a repeated input/config object identity only once', async () => {
        let reads = 0;
        const input = {
            query: 'What is the capital of France?',
            get response() { return ++reads === 1 ? unsupported : supported; },
            context: supported
        };
        const result = await evaluate('faithfulness', [input, input], input);
        expect(reads).toBe(1);
        expect(result.results.eval_results.map(row => row?.output)).toEqual([0, 0]);
    });

    it('R5-LE05 rejects a throwing input getter without inspecting thrown text', async () => {
        let reads = 0;
        let messageReads = 0;
        const thrown = { get message() { messageReads++; return 'CALLER_ERROR_TEXT'; } };
        const input = { get response(): string { reads++; throw thrown; }, code: benignCode.code };
        const result = await evaluate('secrets_detection', [input]);
        expectBoundaryFailure(result);
        expect(reads).toBe(1);
        expect(messageReads).toBe(0);
        expect(JSON.stringify(result)).not.toContain('CALLER_ERROR_TEXT');
    });

    it('R5-LE06 rejects a throwing config getter with a fixed diagnostic', async () => {
        let reads = 0;
        const config = { get threshold(): number { reads++; throw new Error('CALLER_CONFIG_TEXT'); } };
        const result = await evaluate('secrets_detection', [benignCode], config);
        expectBoundaryFailure(result);
        expect(reads).toBe(1);
        expect(JSON.stringify(result)).not.toContain('CALLER_CONFIG_TEXT');
    });

    it('R5-LE07 rejects malformed input records instead of synthesizing evidence', async () => {
        for (const input of [null, false, 'text', []]) {
            expectBoundaryFailure(await evaluate('secrets_detection', [input as any]));
        }
    });

    it('R5-LE08 keeps supported evidence and genuinely omitted config working', async () => {
        const result = await evaluate('faithfulness', [{
            query: 'What is the capital of France?', response: supported, context: supported
        }]);
        expect(result.errors).toEqual([]);
        expect(result.results.eval_results[0]).toMatchObject({
            name: 'faithfulness', output: 1, runtime: 0, output_type: 'score'
        });
        expect(result.executedLocally).toEqual(['faithfulness']);
        expect(result.executedCloud).toEqual([]);
        const clean = await evaluate('secrets_detection', [benignCode]);
        expect(clean.errors).toEqual([]);
        expect(clean.results.eval_results[0]?.output).toBe(1);
    });

    it('R5-LE09 preserves input-over-config precedence and output/text aliases', async () => {
        const result = await evaluate('contains', [
            Object.freeze({ output: 'Hello world', keyword: 'world' }),
            Object.freeze({ text: 'Goodbye world', keyword: 'world' })
        ], Object.freeze({ keyword: 'absent' }));
        expect(result.errors).toEqual([]);
        expect(result.results.eval_results.map(row => row?.output)).toEqual([1, 1]);
    });

    it('R5-LE10 keeps an empty ordinary input batch empty', async () => {
        const result = await evaluate('contains', [], { keyword: 'world' });
        expect(result.results.eval_results).toEqual([]);
        expect(result.errors).toEqual([]);
    });
});

describe('HybridEvaluator partition ownership', () => {
    it('R5-LE11 retains the captured values across partitioning and later evaluation', () => {
        const hybrid = new HybridEvaluator({ offlineMode: true });
        let responseReads = 0;
        let configReads = 0;
        const request = {
            metricName: 'faithfulness',
            inputs: [{
                query: 'What is the capital of France?',
                get response() { return ++responseReads === 1 ? unsupported : supported; },
                context: supported
            }],
            get config() { configReads++; return {}; }
        };
        const partitions = hybrid.partitionEvaluations([request]);
        expect(responseReads).toBe(1);
        expect(configReads).toBe(1);
        const result = hybrid.evaluateLocalPartition(partitions[ExecutionMode.LOCAL]);
        expect(responseReads).toBe(1);
        expect(configReads).toBe(1);
        expect(result.results.eval_results[0]?.output).toBe(0);
        expect(result.errors).toEqual([]);
    });

    it.each(invalidConfigs)('R5-LE12 rejects a %s config while partitioning', (_label, config) => {
        const hybrid = new HybridEvaluator({ offlineMode: true });
        expect(() => hybrid.partitionEvaluations([{
            metricName: 'secrets_detection', inputs: [benignCode], config: config as Record<string, any>
        }])).toThrow('METRIC_CONFIG_INVALID');
    });
});

describe('HybridEvaluator non-heuristic capture routes with offline stubs', () => {
    const judgment = { score: 1, reason: 'offline fixture' };

    it('R5-LE13 captures every row before the first local judge await', async () => {
        let secondResponse = 'before dispatch';
        let secondReads = 0;
        let configReads = 0;
        const judge = jest.fn()
            .mockImplementationOnce(async () => { secondResponse = 'after dispatch'; return judgment; })
            .mockResolvedValue(judgment);
        const hybrid = new HybridEvaluator({ localLLM: { judge } as unknown as OllamaLLM, offlineMode: true });
        const result = await hybrid.evaluate('coherence', [
            { query: 'first query', response: 'first response' },
            { query: 'second query', get response() { secondReads++; return secondResponse; } }
        ], { get threshold() { configReads++; return 0.5; } });
        expect(secondReads).toBe(1);
        expect(configReads).toBe(1);
        expect(judge).toHaveBeenCalledTimes(2);
        expect(judge.mock.calls[1]?.[1]).toBe('before dispatch');
        expect(result.results.eval_results.map(row => row?.output)).toEqual([1, 1]);
        expect(result.errors).toEqual([]);
    });

    it('R5-LE14 captures the cloud input and model config without rereading originals', async () => {
        let responseReads = 0;
        let modelReads = 0;
        const cloudResult = { eval_results: [{ name: 'coherence', output: 1, runtime: 0 }] };
        const evaluate = jest.fn().mockResolvedValue(cloudResult);
        const hybrid = new HybridEvaluator({ cloudEvaluator: { evaluate } as unknown as Evaluator });
        const result = await hybrid.evaluate('coherence', [{
            get response() { return ++responseReads === 1 ? 'first response' : 'later response'; }
        }], { get model() { return ++modelReads === 1 ? 'offline-first-model' : 'offline-later-model'; } });
        expect(responseReads).toBe(1);
        expect(modelReads).toBe(1);
        expect(evaluate).toHaveBeenCalledTimes(1);
        expect(evaluate).toHaveBeenCalledWith('coherence', { response: ['first response'] }, { modelName: 'offline-first-model' });
        expect(result.results).toBe(cloudResult);
        expect(result.executedCloud).toEqual(['coherence']);
        expect(result.errors).toEqual([]);
    });

    it.each(invalidConfigs)('R5-LE15 rejects a %s config without calling either backend', async (_label, config) => {
        const judge = jest.fn().mockResolvedValue(judgment);
        const evaluate = jest.fn().mockResolvedValue({ eval_results: [] });
        const local = new HybridEvaluator({ localLLM: { judge } as unknown as OllamaLLM, offlineMode: true });
        const cloud = new HybridEvaluator({ cloudEvaluator: { evaluate } as unknown as Evaluator });
        expectBoundaryFailure(await local.evaluate('coherence', [{ response: 'text' }], config as Record<string, any>));
        expectBoundaryFailure(await cloud.evaluate('coherence', [{ response: 'text' }], config as Record<string, any>));
        expect(judge).not.toHaveBeenCalled();
        expect(evaluate).not.toHaveBeenCalled();
    });

    it('R5-LE16 preserves omitted backend configs and the cloud default model', async () => {
        const judge = jest.fn().mockResolvedValue(judgment);
        const evaluate = jest.fn().mockResolvedValue({ eval_results: [] });
        const local = new HybridEvaluator({ localLLM: { judge } as unknown as OllamaLLM, offlineMode: true });
        const cloud = new HybridEvaluator({ cloudEvaluator: { evaluate } as unknown as Evaluator });
        expect((await local.evaluate('coherence', [{ response: 'text' }])).errors).toEqual([]);
        expect((await cloud.evaluate('coherence', [{ response: 'text' }])).errors).toEqual([]);
        expect(evaluate).toHaveBeenCalledWith('coherence', { response: ['text'] }, { modelName: 'gpt-4o' });
    });

    it('R5-LE17 never reads a rejected backend object message', async () => {
        let messageReads = 0;
        const thrown = { get message() { messageReads++; return 'BACKEND_ERROR_TEXT'; } };
        const judge = jest.fn().mockRejectedValue(thrown);
        const evaluate = jest.fn().mockRejectedValue(thrown);
        const local = new HybridEvaluator({ localLLM: { judge } as unknown as OllamaLLM, offlineMode: true });
        const cloud = new HybridEvaluator({ cloudEvaluator: { evaluate } as unknown as Evaluator });
        for (const hybrid of [local, cloud]) {
            const result = await hybrid.evaluate('coherence', [{ response: 'text' }]);
            expect(result.errors).toEqual([{ metric: 'coherence', error: 'METRIC_EXECUTION_FAILED' }]);
            expect(JSON.stringify(result)).not.toContain('BACKEND_ERROR_TEXT');
        }
        expect(messageReads).toBe(0);
    });

    it('R5-LE18 contains captured cloud conversion failures before backend dispatch', async () => {
        const evaluate = jest.fn().mockResolvedValue({ eval_results: [] });
        const hybrid = new HybridEvaluator({ cloudEvaluator: { evaluate } as unknown as Evaluator });
        const result = await hybrid.evaluate('coherence', [{ response: { nested: 'text' } }]);
        expect(result.errors).toEqual([{ metric: 'coherence', error: 'METRIC_EXECUTION_FAILED' }]);
        expect(evaluate).not.toHaveBeenCalled();
    });
});

describe('LocalEvaluator fixed diagnostics', () => {
    it('R5-LE19 keeps strict mode throwing without interpolating the metric name', () => {
        const evaluator = new LocalEvaluator({ strictMode: true });
        expect(() => evaluator.evaluate('coherence', [{ response: 'text' }])).toThrow("Metric 'coherence' cannot run locally");
        expect(new LocalEvaluator().evaluate('coherence', [{ response: 'text' }]).errors)
            .toEqual([{ metric: 'coherence', error: 'METRIC_NOT_LOCAL' }]);
    });
});
