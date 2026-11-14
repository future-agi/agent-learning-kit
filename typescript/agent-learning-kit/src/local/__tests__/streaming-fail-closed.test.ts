import { StreamingEvaluator } from '../streaming/evaluator.js';
import * as metrics from '../metrics/index.js';

afterEach(() => jest.restoreAllMocks());

describe('W04 streaming fail-closed', () => {
    test.each(['missing', 'throw', 'invalid', 'transient'])('FO-09 preserves %s metric in final coverage', mode => {
        jest.spyOn(console, 'warn').mockImplementation(() => {});
        jest.spyOn(metrics, 'hasMetric').mockImplementation(name => mode !== 'missing' || name !== 'b');
        let brokenCalls = 0;
        jest.spyOn(metrics, 'runMetric').mockImplementation(name => {
            if (name === 'b') {
                brokenCalls++;
                if (mode === 'missing' || mode === 'throw' || (mode === 'transient' && brokenCalls === 1)) throw new Error('PRIVATE_SENTINEL');
                if (mode === 'invalid') return { score: Infinity, passed: true, reason: 'fixture' };
            }
            return { score: 1, passed: true, reason: 'fixture' };
        });
        const stream = new StreamingEvaluator({ metrics: ['a', 'b'], minCharsBeforeEval: 1 });
        const events: unknown[] = [];
        stream.on(event => events.push(event));
        stream.addChunk('content');
        const result = stream.complete();
        expect(result.finalResults.size).toBe(2);
        expect(result.finalResults.get('b')?.passed).toBe(false);
        expect((result as any).status).toBe('incomplete');
        expect((result as any).coverage.required).toBe(2);
        expect(JSON.stringify(events)).not.toContain('PRIVATE_SENTINEL');
    });

    test('FO-09 zero metrics are incomplete', () => {
        expect((new StreamingEvaluator({ metrics: [] }).complete() as any).status).toBe('incomplete');
    });

    test('FO-09 genuine PASS control, one final execution', () => {
        jest.spyOn(console, 'warn').mockImplementation(() => {});
        jest.spyOn(metrics, 'hasMetric').mockReturnValue(true);
        const run = jest.spyOn(metrics, 'runMetric').mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        const stream = new StreamingEvaluator({ metrics: ['a'], minCharsBeforeEval: 100 });
        stream.addChunk('content');
        const result = stream.complete();
        expect(result.finalResults.get('a')?.passed).toBe(true);
        expect(run).toHaveBeenCalledTimes(1);
    });
});
