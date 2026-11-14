import { StreamingEvaluator } from '../streaming/evaluator.js';
import * as metrics from '../metrics/index.js';
import { secretsDetection } from '../metrics/code/secrets-detection.js';
import { allSecurityChecks } from '../metrics/code/index.js';

// Deliberately synthetic, assembled rather than copied from any credential.
const syntheticValue = ['fixture', 'only', 'value'].join('_');
const hardcoded = () => ['const pass', 'word = "', syntheticValue, '";'].join('');
const safeCode = 'const value = 1;';
const evidence = 'The city library opens every morning.';
const environments = [
    { id: 'js', expression: 'process.env.SYNTHETIC_SETTING' },
    { id: 'py-index', expression: 'os.environ["SYNTHETIC_SETTING"]' },
    { id: 'py-call', expression: 'os.getenv("SYNTHETIC_SETTING")' }
];

function expectHardcoded(result: ReturnType<typeof secretsDetection>): void {
    expect(result.passed).toBe(false);
    expect(result.score).toBeLessThan(1);
    expect(result.issues.some(issue => issue.type === 'hardcoded-password' && issue.severity === 'high')).toBe(true);
    expect(result.issues.every(issue => issue.match === '[REDACTED]')).toBe(true);
    // Boolean assertions keep even failing assertion diagnostics value-free.
    expect(JSON.stringify(result).includes(syntheticValue)).toBe(false);
}

afterEach(() => jest.restoreAllMocks());

describe('W04 r4 adapter regressions', () => {
    test.each(['context_recall', 'CONTEXT_RECALL', 'Context_Recall'])('R4-A01-%s preserves supplied reference and canonical dispatch', name => {
        const run = jest.spyOn(metrics, 'runMetric');
        const stream = new StreamingEvaluator({
            metrics: [name], query: 'When does the library open?', context: evidence,
            reference: evidence, minCharsBeforeEval: 1
        });
        stream.addChunk(evidence);
        // any permits assertion-based baseline RED even before completion metadata existed.
        const result: any = stream.complete();
        expect(run.mock.calls.every(([id, , config]) => id === 'context_recall' && config?.expected_response === evidence)).toBe(true);
        expect(run).toHaveBeenCalledTimes(2);
        expect(result.finalResults.get(name)).toMatchObject({ score: 1, passed: true });
        expect(result.status).toBe('passed');
        expect(result.coverage).toEqual({ required: 1, executed: 1, completed: 1, skipped: 0, errored: 0 });
        expect(result.metricErrors).toEqual([]);
        expect([...result.finalResults.keys()]).toEqual([name]);
    });

    test.each(['context_recall', 'CONTEXT_RECALL'])('R4-A02-%s still requires reference evidence', name => {
        const stream = new StreamingEvaluator({ metrics: [name], query: 'Library hours?', context: evidence, minCharsBeforeEval: 1 });
        stream.addChunk(evidence);
        const result: any = stream.complete();
        expect(result.status).toBe('incomplete');
        expect(result.finalResults.get(name)).toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
        expect(result.metricErrors).toEqual([{ metric: name, reasonCode: 'NO_EVIDENCE' }]);
    });

    test('R4-A03 keeps requested display and threshold keys while routing canonical recall', () => {
        const name = 'CONTEXT_RECALL';
        const events: any[] = [];
        const stream = new StreamingEvaluator({
            metrics: [name], thresholds: { [name]: 0.5 }, query: 'Library hours?',
            context: 'Copper conducts electricity.', reference: evidence, minCharsBeforeEval: 1
        });
        stream.on(event => events.push(event));
        stream.addChunk(evidence);
        const result: any = stream.complete();
        expect(result.status).toBe('failed');
        expect(result.finalResults.get(name)).toMatchObject({ score: 0, passed: false });
        expect(events.filter(event => event.type === 'threshold_alert')).toEqual([
            expect.objectContaining({ metric: name, threshold: 0.5, direction: 'below', message: 'THRESHOLD_BELOW' })
        ]);
    });

    test('R4-A04 truncation remains terminal for a case-insensitive recall request', () => {
        const stream = new StreamingEvaluator({
            metrics: ['CONTEXT_RECALL'], query: 'Library hours?', context: evidence,
            reference: evidence, maxAccumulatedLength: 8, minCharsBeforeEval: 1
        });
        stream.addChunk(evidence);
        const result: any = stream.complete();
        expect(result.status).toBe('incomplete');
        expect(result.finalResponse).toBe(evidence.slice(0, 8));
        expect(result.finalResults.get('CONTEXT_RECALL')).toMatchObject({ score: 0, passed: false, reason: 'TRUNCATED' });
        expect(result.metricErrors).toEqual([{ metric: 'CONTEXT_RECALL', reasonCode: 'TRUNCATED' }]);
    });

    test('R4-A05 incomplete evidence stays latched after a potential later success', () => {
        const run = jest.spyOn(metrics, 'runMetric')
            .mockReturnValueOnce({ score: 0, passed: false, reason: 'NO_EVIDENCE' })
            .mockReturnValue({ score: 1, passed: true, reason: 'fixture' });
        const stream = new StreamingEvaluator({ metrics: ['ONE_LINE'], minCharsBeforeEval: 1 });
        stream.addChunk('first');
        stream.addChunk(' second');
        const result: any = stream.complete();
        expect(result.status).toBe('incomplete');
        expect(result.finalResults.get('ONE_LINE')).toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
        expect(run).toHaveBeenCalledTimes(1);
    });

    test('R4-A06 quality failure stays latched through completion', () => {
        jest.spyOn(metrics, 'runMetric')
            .mockReturnValueOnce({ score: 0, passed: false, reason: 'fixture failure' })
            .mockReturnValue({ score: 1, passed: true, reason: 'fixture success' });
        const stream = new StreamingEvaluator({ metrics: ['ONE_LINE'], minCharsBeforeEval: 1 });
        stream.addChunk('first');
        const result: any = stream.complete();
        expect(result.status).toBe('failed');
        expect(result.finalResults.get('ONE_LINE')).toMatchObject({ score: 0, passed: false, reason: 'CHECK_FAILED' });
    });

    test.each(environments.flatMap(environment => ['before', 'after'].map(order => ({
        ...environment, order, id: `${environment.id}-${order}`
    }))))('R4-A07-$id unrelated same-line environment expression cannot hide a match', ({ expression, order }) => {
        const unrelated = `const region = ${expression};`;
        const code = (order === 'before' ? [unrelated, hardcoded()] : [hardcoded(), unrelated]).join(' ');
        const result = secretsDetection({ code });
        expectHardcoded(result);
        expect(result.issues.some(issue => issue.type === 'hardcoded-password' && issue.line === 1)).toBe(true);
    });

    test.each(environments)('R4-A08-$id legitimate environment reference remains clean', ({ expression }) => {
        const result = secretsDetection({ code: `password = ${expression};` });
        expect(result).toMatchObject({ score: 1, passed: true, issues: [] });
    });

    test('R4-A09 default comments are checked; explicit exclusion still skips comments', () => {
        const code = `// ${hardcoded()} const region = ${environments[0].expression};`;
        expectHardcoded(secretsDetection({ code }));
        expectHardcoded(secretsDetection({ code }, { checkComments: true }));
        expect(secretsDetection({ code }, { checkComments: false })).toMatchObject({ score: 1, passed: true, issues: [] });
    });

    test('R4-A10 isolated synthetic literal remains detected and fully redacted', () => {
        expectHardcoded(secretsDetection({ code: hardcoded() }));
    });

    test('R4-A11 environment references on another line do not change issue locations', () => {
        const result = secretsDetection({ code: `const region = ${environments[0].expression};\n${hardcoded()}` });
        expectHardcoded(result);
        expect(result.issues.some(issue => issue.type === 'hardcoded-password' && issue.line === 2)).toBe(true);
    });

    test('R4-A12 combined checks retain the same-line detector failure', () => {
        const result = allSecurityChecks({ code: `const region = ${environments[0].expression}; ${hardcoded()}` });
        expectHardcoded(result);
    });

    test.each([
        { id: 'true', value: true }, { id: 'false', value: false },
        { id: 'one', value: 1 }, { id: 'zero', value: 0 },
        { id: 'string', value: 'invalid' }, { id: 'empty-string', value: '' },
        { id: 'array', value: [] }, { id: 'null', value: null },
        { id: 'map', value: new Map() }, { id: 'boxed', value: new Number(1) },
        { id: 'function', value: () => ({}) }
    ])('R4-A13-$id rejects a supplied non-record detector config', ({ value }) => {
        let result: ReturnType<typeof secretsDetection> | undefined;
        expect(() => { result = secretsDetection({ code: safeCode }, value as any); }).not.toThrow();
        expect(result).toMatchObject({
            score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID',
            issues: [expect.objectContaining({ type: 'validation-incomplete' })]
        });
    });

    test('R4-A14 omitted, undefined, ordinary and null-prototype records retain defaults', () => {
        expect(secretsDetection({ code: safeCode })).toMatchObject({ score: 1, passed: true, issues: [] });
        for (const config of [undefined, {}, Object.create(null)]) {
            expect(secretsDetection({ code: safeCode }, config)).toMatchObject({ score: 1, passed: true, issues: [] });
        }
    });

    test('R4-A15 changing configuration getter is captured exactly once before defaults', () => {
        let reads = 0;
        const config = Object.defineProperty({}, 'threshold', { get: () => ++reads === 1 ? 1 : 2 });
        const result = secretsDetection({ code: safeCode }, config);
        expect(reads).toBe(1);
        expect(result).toMatchObject({ score: 1, passed: true, issues: [] });
    });

    test('R4-A16 throwing configuration getter produces only fixed diagnostics', () => {
        let reads = 0;
        const config = Object.defineProperty({}, 'threshold', {
            get: () => { reads++; throw new Error('SYNTHETIC_PRIVATE_DIAGNOSTIC'); }
        });
        let result: ReturnType<typeof secretsDetection> | undefined;
        expect(() => { result = secretsDetection({ code: safeCode }, config); }).not.toThrow();
        expect(reads).toBe(1);
        expect(result).toMatchObject({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
        expect(JSON.stringify(result).includes('SYNTHETIC_PRIVATE_DIAGNOSTIC')).toBe(false);
    });

    test('R4-A17 iterable configuration snapshot is rejected as an array without replay', () => {
        let iterations = 0;
        const config = { [Symbol.iterator]() { iterations++; return [][Symbol.iterator](); } };
        const result = secretsDetection({ code: safeCode }, config as any);
        expect(iterations).toBe(1);
        expect(result).toMatchObject({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });

    test('R4-A18 custom pattern iterator remains single-read under a valid record', () => {
        let iterations = 0;
        const customPatterns = { [Symbol.iterator]() { iterations++; return ['unused_fixture_marker'][Symbol.iterator](); } };
        const result = secretsDetection({ code: safeCode }, { customPatterns } as any);
        expect(iterations).toBe(1);
        expect(result).toMatchObject({ score: 1, passed: true, issues: [] });
    });
});
