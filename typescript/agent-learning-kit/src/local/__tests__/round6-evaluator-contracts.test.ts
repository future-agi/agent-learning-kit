/**
 * ALK-R6-EVALUATOR: fixed diagnostics and captured cloud conversion contracts.
 * Authored only; execution belongs to the parent's admitted test venue.
 */
import { HybridEvaluator, LocalEvaluator } from '../evaluator';
import type { Evaluator } from '../../evaluator';
import * as metrics from '../metrics/index';

function cloudFixture() {
    const cloudResult = { eval_results: [{ name: 'coherence', output: 1, runtime: 0 }] };
    const evaluate = jest.fn().mockResolvedValue(cloudResult);
    const hybrid = new HybridEvaluator({ cloudEvaluator: { evaluate } as unknown as Evaluator });
    return { cloudResult, evaluate, hybrid };
}

describe('Round 6 evaluator contracts', () => {
    it('R6-EC01 throws exactly the fixed code for a private multiline unknown metric name', () => {
        const evaluator = new LocalEvaluator({ strictMode: true });
        const metric = 'SYNTHETIC_PRIVATE_METRIC_TEXT\nFORGED_DIAGNOSTIC';
        let thrown: unknown;
        try {
            evaluator.evaluate(metric, [{ response: 'text' }]);
        } catch (error) {
            thrown = error;
        }
        expect(thrown).toBeInstanceOf(Error);
        expect((thrown as Error).message).toBe('METRIC_NOT_LOCAL');
        expect(new LocalEvaluator().evaluate(metric, [{ response: 'text' }]).errors)
            .toEqual([{ metric, error: 'METRIC_NOT_LOCAL' }]);
    });

    it('R6-EC08 keeps a hostile metric name out of the whole error object and console/stderr', () => {
        const evaluator = new LocalEvaluator({ strictMode: true });
        const metric = 'SYNTHETIC_HOSTILE_METRIC_NAME\nFORGED_LINE';
        const spies = ['error', 'warn', 'log', 'info', 'debug'].map(level =>
            jest.spyOn(console, level as 'error').mockImplementation(() => undefined));
        const stderr = jest.spyOn(process.stderr, 'write').mockImplementation(() => true);
        let thrown: unknown;
        try {
            evaluator.evaluate(metric, [{ response: 'text' }]);
        } catch (error) {
            thrown = error;
        } finally {
            spies.forEach(spy => spy.mockRestore());
            stderr.mockRestore();
        }
        expect(thrown).toBeInstanceOf(Error);
        const error = thrown as Error & { cause?: unknown };
        expect(error.message).toBe('METRIC_NOT_LOCAL');
        expect(error.cause).toBeUndefined();
        for (const key of Object.getOwnPropertyNames(error)) {
            expect(String((error as unknown as Record<string, unknown>)[key])).not.toContain('SYNTHETIC_HOSTILE_METRIC_NAME');
        }
        expect(String(error)).not.toContain('SYNTHETIC_HOSTILE_METRIC_NAME');
        for (const spy of [...spies, stderr]) {
            expect(JSON.stringify(spy.mock.calls)).not.toContain('SYNTHETIC_HOSTILE_METRIC_NAME');
        }
    });

    it('R6-EC02 sends a primitive response and an ordinary record-valued additional field to cloud', async () => {
        const { cloudResult, evaluate, hybrid } = cloudFixture();
        const result = await hybrid.evaluate('coherence', [{
            response: 'valid text', metadata: { source: 'fixture' }
        }]);
        expect(evaluate).toHaveBeenCalledTimes(1);
        expect(evaluate).toHaveBeenCalledWith('coherence', {
            response: ['valid text'], metadata: ['[object Object]']
        }, { modelName: 'gpt-4o' });
        expect(result.results).toBe(cloudResult);
        expect(result.errors).toEqual([]);
        expect(result.executedLocally).toEqual([]);
        expect(result.executedCloud).toEqual(['coherence']);
    });

    it('R6-EC03 preserves ordinary nested-array and primitive cloud field conversion', async () => {
        const { evaluate, hybrid } = cloudFixture();
        const result = await hybrid.evaluate('coherence', [{
            response: 'valid text',
            tags: ['start', [{ source: 'fixture' }, null, 2], undefined, false],
            empty: [],
            nestedEmpty: [[], {}],
            absent: undefined,
            nil: null,
            count: 0,
            enabled: false
        }]);
        expect(evaluate).toHaveBeenCalledTimes(1);
        expect(evaluate).toHaveBeenCalledWith('coherence', {
            response: ['valid text'],
            tags: ['start,[object Object],,2,,false'],
            empty: [''],
            nestedEmpty: [',[object Object]'],
            absent: ['undefined'],
            nil: ['null'],
            count: ['0'],
            enabled: ['false']
        }, { modelName: 'gpt-4o' });
        expect(result.errors).toEqual([]);
    });

    it('R6-EC04 converts only the first captured field and nested element values', async () => {
        const { evaluate, hybrid } = cloudFixture();
        let responseReads = 0;
        let metadataReads = 0;
        let sourceReads = 0;
        let tagsReads = 0;
        let elementReads = 0;
        const metadata = { get source() { sourceReads++; return 'fixture'; } };
        const nested = ['placeholder'];
        Object.defineProperty(nested, '0', {
            enumerable: true,
            get() { return ++elementReads === 1 ? 'first element' : 'later element'; }
        });
        const tags = [metadata, nested];
        const input = {
            get response() { return ++responseReads === 1 ? 'first response' : 'later response'; },
            get metadata() { metadataReads++; return metadata; },
            get tags() { tagsReads++; return tags; }
        };
        const result = await hybrid.evaluate('coherence', [input]);
        expect(responseReads).toBe(1);
        expect(metadataReads).toBe(1);
        expect(sourceReads).toBe(1);
        expect(tagsReads).toBe(1);
        expect(elementReads).toBe(1);
        expect(evaluate).toHaveBeenCalledTimes(1);
        expect(evaluate).toHaveBeenCalledWith('coherence', {
            response: ['first response'],
            metadata: ['[object Object]'],
            tags: ['[object Object],first element']
        }, { modelName: 'gpt-4o' });
        expect(result.errors).toEqual([]);
    });

    it('R6-EC05 keeps local metrics and genuinely omitted configs working', async () => {
        for (const evaluator of [new LocalEvaluator(), new HybridEvaluator({ offlineMode: true })]) {
            const contains = await evaluator.evaluate('contains', [{ response: 'Hello world' }], { keyword: 'world' });
            expect(contains.errors).toEqual([]);
            expect(contains.results.eval_results[0]?.output).toBe(1);
            expect(contains.executedLocally).toEqual(['contains']);
            expect(contains.executedCloud).toEqual([]);
            const json = await evaluator.evaluate('is_json', [{ response: '{"valid":true}' }]);
            expect(json.errors).toEqual([]);
            expect(json.results.eval_results[0]?.output).toBe(1);
            expect(json.executedLocally).toEqual(['is_json']);
            expect(json.executedCloud).toEqual([]);
        }
    });

    it('R6-EC06 keeps a genuine metric throw distinct from a zero-score diagnostic result', () => {
        let messageReads = 0;
        const thrown = { get message() { messageReads++; return 'SYNTHETIC_METRIC_ERROR_TEXT'; } };
        const runMetric = jest.spyOn(metrics, 'runMetric').mockImplementationOnce(() => { throw thrown; });
        try {
            const result = new LocalEvaluator().evaluate('contains', [{ response: 'text' }], { keyword: 'text' });
            expect(runMetric).toHaveBeenCalledTimes(1);
            expect(runMetric).toHaveBeenCalledWith('contains', 'text', { keyword: 'text', response: 'text' });
            expect(result).toEqual({
                results: { eval_results: [{
                    name: 'contains', output: null, reason: 'METRIC_EXECUTION_FAILED', runtime: 0
                }] },
                executedLocally: ['contains'],
                executedCloud: [],
                errors: [{ metric: 'contains', error: 'METRIC_EXECUTION_FAILED' }]
            });
            expect(messageReads).toBe(0);
        } finally {
            runMetric.mockRestore();
        }
    });

    it('R6-EC07 retains the invalid-regex zero/false leaf result and its exact wrapper projection', () => {
        expect(metrics.runMetric('regex', 'test', { pattern: '[invalid' })).toEqual({
            score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID'
        });
        const result = new LocalEvaluator().evaluate('regex', [{ response: 'test' }], { pattern: '[invalid' });
        expect(result).toEqual({
            results: { eval_results: [{
                name: 'regex', output: 0, reason: 'METRIC_CONFIG_INVALID', runtime: 0, output_type: 'score'
            }] },
            executedLocally: ['regex'],
            executedCloud: [],
            errors: []
        });
    });
});
