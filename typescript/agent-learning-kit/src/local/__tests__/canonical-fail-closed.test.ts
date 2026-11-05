import { AutoEvalPipeline } from '../autoeval/pipeline.js';
import { Scanner } from '../scanner/scanner.js';
import { StreamingEvaluator } from '../streaming/evaluator.js';
import * as selector from '../autoeval/selector.js';
import * as metrics from '../metrics/index.js';
import { sqlInjection } from '../metrics/code/sql-injection.js';
import { xssDetection } from '../metrics/code/xss-detection.js';
import { secretsDetection } from '../metrics/code/secrets-detection.js';
import { answerRelevance } from '../metrics/rag/answer-relevance.js';
import { contextRelevance, contextUtilization } from '../metrics/rag/context-relevance.js';
import { contextRecall } from '../metrics/rag/context-recall.js';
import { faithfulness } from '../metrics/rag/faithfulness.js';
import { hallucinationDetection } from '../metrics/hallucination/detection.js';

const selected = [{ name: 'one_line', category: 'string' as const, confidence: 1, reason: 'fixture' }];
const rule = { id: 'fixture', name: 'fixture', description: 'fixture', category: 'quality' as const, severity: 'high' as const, enabledByDefault: true, check: jest.fn(() => []) };
const once = <T>(values: T[]): Iterable<T> => {
    let iterations = 0;
    return { [Symbol.iterator]() { if (++iterations > 1) throw new Error('PRIVATE_SENTINEL'); return values[Symbol.iterator](); } };
};
afterEach(() => jest.restoreAllMocks());

describe('W04 r2 canonical boundaries', () => {
    test('R2-C07 RAG and hallucination thresholds are finite number primitives', () => {
        const input = { query: 'Paris capital', response: 'Paris is the capital of France.', context: 'Paris is the capital of France.', expected_response: 'Paris is the capital of France.' };
        for (const run of [answerRelevance, contextRelevance, contextUtilization, contextRecall, faithfulness, hallucinationDetection]) {
            expect(run(input, { threshold: new Number(0) } as any)).toMatchObject({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
            expect(run(input, { threshold: 0 }).passed).toBe(true);
        }
    });
    test.each([{ id: 'FO-10', run: sqlInjection }, { id: 'FO-11', run: xssDetection }, { id: 'FO-12', run: secretsDetection }])('R2-C08 $id severity and thresholds are canonical before filtering', ({ run }) => {
        expect(run({ code: 'const value = 1;' }, { minSeverity: new String('critical') } as any))
            .toMatchObject({ passed: false, reason: 'METRIC_CONFIG_INVALID' });
        expect(run({ code: 'const value = 1;' }, { threshold: new Number(0) } as any).passed).toBe(false);
    });
    test('R2-C10 streaming snapshots a one-shot metric iterable once', () => {
        jest.spyOn(metrics, 'runMetric').mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        let result: any;
        expect(() => {
            const stream = new StreamingEvaluator({ metrics: once(['one_line']) as any, minCharsBeforeEval: 1 });
            stream.addChunk('answer');
            result = stream.complete();
        }).not.toThrow();
        expect(result.status).toBe('passed');
        expect(result.coverage.required).toBe(1);
    });
    test('R2-C09 streaming rejects boxed thresholds before executing metrics', () => {
        expect(() => new StreamingEvaluator({ metrics: ['one_line'], thresholds: { one_line: new Number(0) } } as any)).toThrow('STREAM_CONFIG_INVALID');
    });
    test('R2-C01 AutoEval rejects boxed reason, score and verdict, retains genuine PASS', () => {
        jest.spyOn(selector, 'selectMetrics').mockReturnValue(selected);
        jest.spyOn(metrics, 'hasMetric').mockReturnValue(true);
        const run = jest.spyOn(metrics, 'runMetric');
        for (const invalid of [{ reason: new String('NO_EVIDENCE') }, { score: new Number(1) }, { passed: new Boolean(true) }]) {
            run.mockReturnValue({ score: 1, passed: true, reason: 'fixture', ...invalid } as any);
            expect(new AutoEvalPipeline({ passThreshold: 0 }).evaluate({ response: 'answer' }).passed).toBe(false);
        }
        run.mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        expect(new AutoEvalPipeline().evaluate({ response: 'answer' }).passed).toBe(true);
    });
    test('R2-C02 AutoEval snapshots category iterables once before selection', () => {
        const pipeline = new AutoEvalPipeline({ includeCategories: once(['string']) as any });
        let result: ReturnType<AutoEvalPipeline['evaluate']> | undefined;
        expect(() => { result = pipeline.evaluate({ response: 'answer' }); }).not.toThrow();
        expect(result?.passed).toBe(true);
        expect(pipeline.evaluate({ response: 'answer' }).passed).toBe(true);
    });
    test('R2-C03 scanner snapshots rules once and retains unknown requested coverage', () => {
        let result: any;
        expect(() => { result = new Scanner({ customRules: [rule], enableRules: once(['fixture', 'missing']) as any }).scan('answer'); }).not.toThrow();
        expect(result?.passed).toBe(false);
        expect(result?.coverage?.required).toBe(2);
        expect(new Scanner({ customRules: [rule], enableRules: once(['fixture']) as any }).scan('answer').passed).toBe(true);
    });
    test('R2-C04 scanner rejects boxed severity before property lookup', () => {
        const custom = { ...rule, check: () => [{ severity: new String('high'), confidence: 1, category: 'quality' }] } as any;
        const result = new Scanner({ customRules: [custom], enableRules: ['fixture'], failOnSeverity: 'critical' }).scan('answer');
        expect(result.passed).toBe(false);
        expect((result as any).ruleErrors?.[0].reasonCode).toBe('RULE_RESULT_INVALID');
    });
    test('R2-C05 streaming rejects boxed reason and latches it through completion', () => {
        jest.spyOn(metrics, 'hasMetric').mockReturnValue(true);
        jest.spyOn(metrics, 'runMetric').mockReturnValue({ score: 1, passed: true, reason: new String('NO_EVIDENCE') } as any);
        const stream = new StreamingEvaluator({ metrics: ['one_line'], minCharsBeforeEval: 1 });
        stream.addChunk('answer');
        const result = stream.complete() as any;
        expect(result.status).toBe('incomplete');
        expect(result.finalResults.get('one_line')?.passed).toBe(false);
        expect(result.coverage?.required).toBe(1);
    });
    test.each([{ id: 'FO-10', run: sqlInjection }, { id: 'FO-11', run: xssDetection }, { id: 'FO-12', run: secretsDetection }])('R2-C06 $id snapshots patterns and validates string primitives', ({ run }) => {
        const result = run({ code: 'const value = 1;' }, { customPatterns: once([new String('never_match')]) as any, threshold: 0 });
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('INVALID_SECURITY_PATTERN');
        expect(run({ code: 'const value = 1;' }, { customPatterns: once(['never_match']) as any }).passed).toBe(true);
    });
});
