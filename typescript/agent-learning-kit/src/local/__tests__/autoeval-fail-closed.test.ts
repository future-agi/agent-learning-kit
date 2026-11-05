import { AutoEvalPipeline } from '../autoeval/pipeline.js';
import * as selector from '../autoeval/selector.js';
import * as metrics from '../metrics/index.js';

const input = { response: 'A complete synthetic answer.' };
const selected = (name: string) => ({ name, category: 'security' as const, confidence: 1, reason: 'fixture' });

afterEach(() => jest.restoreAllMocks());

describe('W04 AutoEval fail-closed', () => {
    test.each([
        { maxMetrics: 0 },
        { includeCategories: [] },
        { excludeCategories: ['string', 'json', 'similarity', 'rag', 'security', 'hallucination', 'quality'] },
    ])('FO-01 zero selection never passes even at threshold zero: %p', config => {
        const result = new AutoEvalPipeline({ ...config, passThreshold: 0 } as any).evaluate(input);
        expect(result.passed).toBe(false);
        expect(result.overallScore).toBe(0);
        expect((result as any).status).toBe('incomplete');
    });

    test('FO-01 empty evidence cannot inflate coverage', () => {
        jest.spyOn(selector, 'selectMetrics').mockReturnValue([selected('one_line')]);
        jest.spyOn(metrics, 'runMetric').mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        const result = new AutoEvalPipeline({ passThreshold: 0 }).evaluate({ response: '   ' });
        expect(result.passed).toBe(false);
        expect((result as any).coverage.completed).toBe(0);
    });

    test.each(['missing', 'throw', 'mixed-error', 'mixed-failure', 'invalid-result'])('FO-02 retains required %s outcome', mode => {
        jest.spyOn(selector, 'selectMetrics').mockReturnValue([selected('a'), selected('b')]);
        jest.spyOn(metrics, 'hasMetric').mockImplementation(name => mode !== 'missing' || name !== 'b');
        jest.spyOn(metrics, 'runMetric').mockImplementation(name => {
            if (mode === 'throw' || (mode === 'mixed-error' && name === 'b')) throw new Error('PRIVATE_SENTINEL');
            if (mode === 'invalid-result' && name === 'b') return { score: NaN, passed: 'yes' } as any;
            return { score: 1, passed: !(mode === 'mixed-failure' && name === 'b'), reason: 'fixture' };
        });
        const result = new AutoEvalPipeline({ passThreshold: 0 }).evaluate(input);
        expect(result.passed).toBe(false);
        expect(result.metricResults).toHaveLength(2);
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
        expect((result as any).coverage.required).toBe(2);
    });

    test.each([0, -1, NaN, Infinity, -Infinity])('FO-02 invalid weight %p is not usable evidence', weight => {
        jest.spyOn(selector, 'selectMetrics').mockReturnValue([selected('a')]);
        jest.spyOn(metrics, 'hasMetric').mockReturnValue(true);
        jest.spyOn(metrics, 'runMetric').mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        const result = new AutoEvalPipeline({ metricWeights: { a: weight }, passThreshold: 0 }).evaluate(input);
        expect(result.passed).toBe(false);
        expect(result.overallScore).toBe(0);
    });

    test('FO-02 genuine completed PASS control', () => {
        jest.spyOn(selector, 'selectMetrics').mockReturnValue([selected('a')]);
        jest.spyOn(metrics, 'hasMetric').mockReturnValue(true);
        jest.spyOn(metrics, 'runMetric').mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        expect(new AutoEvalPipeline().evaluate(input).passed).toBe(true);
    });

    test('FO-01 no extracted claims is not completed evidence', () => {
        jest.spyOn(selector, 'selectMetrics').mockReturnValue([{ ...selected('faithfulness'), category: 'rag' }]);
        const result = new AutoEvalPipeline({ passThreshold: 0 }).evaluate({ query: 'Question', response: '...', context: 'A context.' });
        expect(result.passed).toBe(false);
        expect((result as any).coverage.completed).toBe(0);
    });
});
