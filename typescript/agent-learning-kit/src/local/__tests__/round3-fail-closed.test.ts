import { AbstractLLM } from '../llm/base.js';
import { AutoEvalPipeline } from '../autoeval/pipeline.js';
import { Scanner } from '../scanner/scanner.js';
import { StreamingEvaluator } from '../streaming/evaluator.js';
import { allSecurityChecks, sqlInjection, xssDetection, secretsDetection } from '../metrics/code/index.js';
import { faithfulness } from '../metrics/rag/faithfulness.js';
import { contextRecall } from '../metrics/rag/context-recall.js';
import { contextRelevance, contextUtilization } from '../metrics/rag/context-relevance.js';
import { answerRelevance } from '../metrics/rag/answer-relevance.js';
import { hallucinationDetection } from '../metrics/hallucination/detection.js';
import { bleuScore, rougeScore, recallScore, levenshteinSimilarity, numericSimilarity, semanticListContains } from '../metrics/similarity.js';
import { jsonSchema } from '../metrics/json.js';
import * as metrics from '../metrics/index.js';

class Judge extends AbstractLLM {
    readonly provider = 'fixture'; readonly model = 'fixture';
    chat = jest.fn<Promise<string>, any[]>().mockResolvedValue('{}');
    async generate() { return ''; }
    async isAvailable() { return true; }
    parse(text: string, scale?: number) { return (this.parseJudgeResponse as any)(text, scale); }
    validate(value: unknown) { return this.validateJudgeResult(value, ''); }
}
const evidence = { query: 'Paris capital France', response: 'Paris is the capital of France.', context: 'Paris is the capital of France.', expected_response: 'Paris is the capital of France.' };
function once<T extends object>(record: T): T {
    const counts = new Map<PropertyKey, number>();
    return new Proxy(record, { get(target, key, receiver) {
        if (typeof key === 'symbol') return Reflect.get(target, key, receiver);
        const n = (counts.get(key) ?? 0) + 1; counts.set(key, n);
        if (n > 1) throw new Error('PRIVATE_SENTINEL');
        return Reflect.get(target, key, receiver);
    } });
}
afterEach(() => jest.restoreAllMocks());

describe('W04 round3 fail-closed', () => {
    test('R3-L01 declared scale, not observed magnitude, controls verdict', async () => {
        const judge = new Judge();
        expect(judge.parse('{"score":7,"passed":true}', 100)).toMatchObject({ score: 0.07, passed: false });
        expect(judge.parse('{"score":1,"passed":true}', 10)).toMatchObject({ score: 0.1, passed: false });
        expect(judge.parse('{"score":0.7,"passed":true}', 1)).toMatchObject({ score: 0.7, passed: true });
        judge.chat.mockResolvedValue('{"score":7,"passed":true}');
        expect(await judge.judge('q', 'a', 'quality')).toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
    });
    test('R3-L02 every candidate is counted, fenced or unfenced', () => {
        const judge = new Judge();
        for (const raw of ['quoted ```json\n{"score":1,"passed":true}\n``` actual ```json\n{"score":0,"passed":false}\n```', 'quoted {"score":1,"passed":true} actual {"score":0,"passed":false}', '{"score":1,"quote":{"passed":false}}']) {
            expect(judge.parse(raw)).toMatchObject({ passed: false, reason: 'NO_EVIDENCE' });
        }
        expect(judge.parse('```json\n{"score":0.7,"passed":true}\n```')).toMatchObject({ passed: true, score: 0.7 });
    });
    test('R3-S11 nonenumerable verdicts cannot disappear', () => {
        const judge = new Judge();
        for (const passed of [false, 'true']) {
            expect(judge.validate(Object.defineProperty({ score: 1 }, 'passed', { value: passed })).passed).toBe(false);
        }
        expect(judge.validate(once({ score: 1, passed: true })).passed).toBe(true);
    });
    test('R3-L03 truncation and ordinary failed judgments latch', () => {
        const stream = new StreamingEvaluator({ metrics: ['one_line'], maxAccumulatedLength: 8 });
        stream.addChunk('unsafe\n' + 'a'.repeat(20));
        const result = stream.complete();
        expect(result.status).toBe('incomplete');
        expect(result.metricErrors).toContainEqual({ metric: 'one_line', reasonCode: 'TRUNCATED' });
        const run = jest.spyOn(metrics, 'runMetric').mockReturnValueOnce({ score: 0, passed: false, reason: 'fixture' }).mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        const latch = new StreamingEvaluator({ metrics: ['one_line'], minCharsBeforeEval: 1 });
        latch.addChunk('answer'); latch.addChunk(' later');
        expect(latch.complete().status).toBe('failed');
        expect(run).toHaveBeenCalled();
    });
    test('R3-C04 empty prefixes defer evidence, tokenless metric results latch', () => {
        const stream = new StreamingEvaluator({ metrics: ['one_line'], minCharsBeforeEval: 0 });
        stream.addChunk(''); stream.addChunk('  '); stream.addChunk('A complete answer.');
        expect(stream.complete().status).toBe('passed');
        const rouge = new StreamingEvaluator({ metrics: ['rouge_score'], reference: 'alpha beta', minCharsBeforeEval: 1 });
        rouge.addChunk('---'); rouge.addChunk('alpha beta');
        expect(rouge.complete().status).toBe('incomplete');
    });
    test('R3-L04 scanner never promotes tokenless or claims-free evidence', () => {
        for (const response of ['Paris is the capital of France.', 'I think this seems nice.']) {
            expect(new Scanner().scanRAG(response, '---')).toMatchObject({ passed: false, status: 'incomplete' });
        }
        expect(new Scanner().scanRAG(evidence.response, evidence.context).passed).toBe(true);
    });
    test('R3-S01 combined checks reject invalid severity and share one-shot patterns', () => {
        expect(allSecurityChecks({ code: '' }, { minSeverity: 'unknown' } as any)).toMatchObject({ passed: false, reason: 'METRIC_CONFIG_INVALID' });
        const config = { minSeverity: 'high' as const, threshold: 0 };
        const list = allSecurityChecks({ code: 'dangerous_call' }, { ...config, customPatterns: ['dangerous_call'] });
        const iter = allSecurityChecks({ code: 'dangerous_call' }, { ...config, customPatterns: (function* () { yield 'dangerous_call'; })() as any });
        expect(iter.passed).toBe(false); expect(iter).toEqual(list);
        expect(allSecurityChecks({ code: 'const value = 1;' }).passed).toBe(true);
    });
    test('R3-S04 tokenless claims and zero support threshold never fabricate support', () => {
        expect(faithfulness({ ...evidence, response: '---' }, { threshold: 0 })).toMatchObject({ passed: false, reason: 'NO_EVIDENCE' });
        expect(faithfulness(evidence, { similarityThreshold: 0 } as any)).toMatchObject({ passed: false, reason: 'METRIC_CONFIG_INVALID' });
        expect(faithfulness(evidence).passed).toBe(true);
    });
    test('R3-S08 boxed schema nodes and inherited required names reject', () => {
        for (const schema of [{ properties: { x: new Boolean(false) } }, { items: new Boolean(false) }]) {
            expect(jsonSchema('{"x":1}', { schema })).toMatchObject({ passed: false, reason: 'SCHEMA_UNSUPPORTED' });
        }
        for (const key of ['constructor', 'toString', '__proto__']) {
            expect(jsonSchema('{}', { schema: { type: 'object', required: [key] } }).passed).toBe(false);
        }
        expect(jsonSchema('{"constructor":1}', { schema: { type: 'object', required: ['constructor'] } }).passed).toBe(true);
    });
    test('R3-S12 explicit null is not an omitted configuration value', () => {
        for (const run of [sqlInjection, xssDetection, secretsDetection]) {
            expect(run({ code: 'const value = 1;' }, { threshold: null, minSeverity: null } as any)).toMatchObject({ passed: false, reason: 'METRIC_CONFIG_INVALID' });
        }
        expect(hallucinationDetection(evidence, { includeClaimAnalysis: null } as any)).toMatchObject({ passed: false, reason: 'METRIC_CONFIG_INVALID' });
        expect(faithfulness(evidence, { threshold: null } as any)).toMatchObject({ passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });
    test('R3-C05 sparse weights are valid, positive zero-precision orders are smoothed', () => {
        const result = bleuScore('alpha', { reference: 'alpha' });
        expect(result.score).toBeCloseTo(0.1778279410038923, 12);
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('BLEU score: 0.1778');
        expect(bleuScore('alpha', { reference: 'alpha', ngramWeights: [1, 0, 0, 0] })).toMatchObject({ passed: true, score: 1 });
        expect(bleuScore('alpha', { reference: 'alpha', ngramWeights: [0, 0] }).passed).toBe(false);
    });
    test('R3-C06 chunk score slots and indices retain source positions', () => {
        const input = { ...evidence, query: 'machine learning', response: 'machine learning', context: ['', 'machine learning'] };
        for (const run of [contextRelevance, contextUtilization]) {
            const result = run(input);
            expect(result.relevantIndices).toEqual([1]);
            expect(result.chunkScores).toHaveLength(2);
            expect(result.chunkScores?.[0]).toBe(0);
            // The baseline preserved indices, but lacked canonical no-evidence diagnostics.
            expect(run({ ...input, context: [] })).toMatchObject({ passed: false, reason: 'NO_EVIDENCE' });
        }
    });
    test('R3-C01 real recall adapters consume supplied reference', () => {
        const stream = new StreamingEvaluator({ metrics: ['context_recall'], query: evidence.query, context: evidence.context, reference: evidence.response });
        stream.addChunk(evidence.response);
        expect(stream.complete().finalResults.get('context_recall')).toMatchObject({ passed: true, score: 1, chunkScores: [1] });
        const result = new AutoEvalPipeline({ includeCategories: ['rag'], passThreshold: 0 }).evaluate({ ...evidence, reference: evidence.response });
        expect(result.metricResults.find(r => r.metric === 'context_recall')?.result).toMatchObject({ passed: true, score: 1 });
        expect(result.status).not.toBe('incomplete');
    });
    test('R3-L07 caps account for omitted applicable metrics and cannot drop security', () => {
        const result = new AutoEvalPipeline({ maxMetrics: 1 }).evaluate({ response: '{"ok":true}', code: 'const value = 1;' });
        expect(result.selectedMetrics.some(m => m.name === 'secrets_detection')).toBe(true);
        expect(result.coverage?.skipped).toBeGreaterThan(0);
        expect(result.status).toBe('incomplete');
        expect(result.metricResults.some(r => r.reasonCode === 'METRIC_LIMIT')).toBe(true);
    });
    test('R3-L11 checkComments true checks comments in all detectors', () => {
        const cases = [
            [sqlInjection, '// db.query("SELECT * FROM users WHERE id=" + userId);'],
            [xssDetection, '// element.innerHTML = userInput;'],
            [secretsDetection, '// const password = "fixture-only-value";'],
        ] as const;
        for (const [run, code] of cases) {
            expect(run({ code }, { checkComments: true }).passed).toBe(false);
            expect(run({ code }, { checkComments: false }).passed).toBe(true);
        }
    });
    test('R3-B01 all granted metric boundaries read caller fields once', () => {
        expect(() => {
        for (const run of [sqlInjection, xssDetection, secretsDetection, allSecurityChecks]) {
            expect(run(once({ code: 'const value = 1;' }), once({ threshold: 1 })).passed).toBe(true);
            const aliased = once({ code: 'const value = 1;', customPatterns: (function* () { yield 'never_match'; })() as any });
            expect(run(aliased, aliased).passed).toBe(true);
        }
        for (const run of [faithfulness, contextRecall, contextRelevance, contextUtilization, answerRelevance, hallucinationDetection]) {
            expect(run(once({ ...evidence }), once({ threshold: 0 })).passed).toBe(true);
        }
        for (const run of [bleuScore, rougeScore, recallScore, levenshteinSimilarity]) {
            expect(run(evidence.response, once({ reference: evidence.response })).passed).toBe(true);
        }
        expect(numericSimilarity('1', once({ reference: 1, tolerance: 0 })).passed).toBe(true);
        expect(semanticListContains('alpha', once({ items: ['alpha'], threshold: 1 })).passed).toBe(true);
        expect(jsonSchema('{}', once({ schema: { type: 'object' } })).passed).toBe(true);
        }).not.toThrow();
    });
    test('R3-B02 pipelines and scanner own nested inputs before use', () => {
        expect(() => {
        const pipeline = new AutoEvalPipeline(once({ includeCategories: ['string'] as any }));
        expect(pipeline.evaluate(once({ response: 'A complete answer.' })).passed).toBe(true);
        const stream = new StreamingEvaluator(once({ metrics: ['one_line'], minCharsBeforeEval: 0 }));
        stream.addChunk('A complete answer.'); expect(stream.complete().status).toBe('passed');
        const scanner = new Scanner(once({ enableRules: ['quality/hallucination'], context: once({ ragContext: evidence.context }) }));
        expect(scanner.scan(evidence.response).passed).toBe(true);
        expect(() => pipeline.evaluate({ get response(): string { throw new Error('PRIVATE_SENTINEL'); } })).toThrow('EVALUATION_INPUT_INVALID');
        }).not.toThrow();
    });
});
