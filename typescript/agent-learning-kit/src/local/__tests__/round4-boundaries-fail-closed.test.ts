import * as ts from 'typescript';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as strings from '../metrics/string';
import { contextPrecision } from '../metrics/rag/context-precision';
import { faithfulness, groundedness } from '../metrics/rag/faithfulness';
import { answerRelevance } from '../metrics/rag/answer-relevance';
import { contextRecall } from '../metrics/rag/context-recall';
import { contextRelevance, contextUtilization } from '../metrics/rag/context-relevance';
import { hallucinationDetection, detectHallucination, noHallucination } from '../metrics/hallucination/detection';
import { sqlInjection, noSqlInjection } from '../metrics/code/sql-injection';
import { xssDetection, noXss } from '../metrics/code/xss-detection';
import { containsJson, isJson, jsonSchema } from '../metrics/json';
import { runMetric, getMetric, hasMetric, getAvailableMetrics } from '../metrics';
import { analyzeContent, describeCharacteristics } from '../autoeval/analyzer';
import { selectMetrics, buildMetricConfig, getRecommendedMetrics } from '../autoeval/selector';

const text = 'Paris is the capital of France.';
const rag = { query: text, response: text, context: [text], expected_response: text };
const invalid = { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
const noEvidence = { score: 0, passed: false, reason: 'NO_EVIDENCE' };
const malformed = [true, false, [], null, 1, 0, 'config'];
const stringCases: Array<[string, (response: string, config: any) => any, any]> = [
    ['regex', strings.regex, { pattern: '^Paris' }],
    ['contains', strings.contains, { keyword: 'Paris' }],
    ['containsAll', strings.containsAll, { keywords: ['Paris', 'France'] }],
    ['containsAny', strings.containsAny, { keywords: ['Paris'] }],
    ['containsNone', strings.containsNone, { keywords: ['Berlin'] }],
    ['equals', strings.equals, { expected: text }],
    ['startsWith', strings.startsWith, { prefix: 'Paris' }],
    ['endsWith', strings.endsWith, { suffix: 'France.' }],
    ['lengthLessThan', strings.lengthLessThan, { maxLength: 100 }],
    ['lengthGreaterThan', strings.lengthGreaterThan, { minLength: 1 }],
    ['lengthBetween', strings.lengthBetween, { minLength: 1, maxLength: 100 }],
];
const defaultCases: Array<[string, (input: any, config?: any) => any, any]> = [
    ['contextPrecision', contextPrecision, rag],
    ['faithfulness', faithfulness, rag], ['groundedness', groundedness, rag],
    ['answerRelevance', answerRelevance, rag], ['contextRecall', contextRecall, rag],
    ['contextRelevance', contextRelevance, rag], ['contextUtilization', contextUtilization, rag],
    ['hallucinationDetection', hallucinationDetection, rag],
    ['detectHallucination', detectHallucination, rag], ['noHallucination', noHallucination, rag],
    ['sqlInjection', sqlInjection, { code: 'const total = 1;' }],
    ['noSqlInjection', noSqlInjection, { code: 'const total = 1;' }],
    ['xssDetection', xssDetection, { code: 'const total = 1;' }],
    ['noXss', noXss, { code: 'const total = 1;' }],
];

// Real Proxy traps: ordinary fields can be read once, never again downstream.
function once<T extends object>(value: T): { value: T; reads: Map<PropertyKey, number> } {
    const reads = new Map<PropertyKey, number>();
    return { reads, value: new Proxy(value, {
        get(target, key, receiver) {
            // Array iterators legitimately reread length while materializing;
            // the caller-controlled elements still must be captured just once.
            if (typeof key === 'string' && !(Array.isArray(target) && key === 'length') &&
                Object.prototype.hasOwnProperty.call(target, key)) {
                const count = (reads.get(key) ?? 0) + 1;
                reads.set(key, count);
                if (count > 1) throw new Error('caller-private-marker');
            }
            return Reflect.get(target, key, receiver);
        }
    }) };
}
// A legacy throw is an explicit no-throw assertion RED, never a load-error RED.
function result<T>(operation: () => T): T {
    let value!: T;
    expect(() => { value = operation(); }).not.toThrow();
    return value;
}
function throwing(field: string): any {
    return Object.defineProperty({}, field, { get() { throw new Error('caller-private-marker'); } });
}

// Stable IDs deliberately use only public exports that exist at d251d8f5.
describe('round 4 direct-entry boundaries', () => {
    test('R4-B01 changing regex getter cannot replace the checked pattern', () => {
        let reads = 0;
        const config = { get pattern() { return ++reads === 1 ? '^blocked$' : '.*'; } };
        expect(strings.regex('safe', config)).toMatchObject({ score: 0, passed: false });
        expect(reads).toBe(1);
    });
    test.each(stringCases)('R4-B02 %s reads every captured config field once', (_name, fn, config) => {
        const captured = once(config);
        expect(result(() => fn(text, captured.value)).passed).toBe(true);
        expect([...captured.reads.values()].every(count => count === 1)).toBe(true);
    });
    test.each(stringCases)('R4-B03 %s refuses malformed containers and thrown getters', (_name, fn, config) => {
        for (const value of malformed) expect(result(() => fn(text, value))).toMatchObject(invalid);
        expect(result(() => fn(text, throwing(Object.keys(config)[0])))).toMatchObject(invalid);
    });
    test.each(stringCases)('R4-B04 %s keeps genuine primitive text and refuses empty evidence', (_name, fn, config) => {
        expect(fn(text, config).passed).toBe(true);
        expect(fn('  ', config)).toMatchObject(noEvidence);
    });
    test('R4-B05 keyword sets cannot pass vacuously or produce NaN', () => {
        for (const fn of [strings.containsAll, strings.containsAny, strings.containsNone]) {
            for (const keywords of [[], [''], ['   ']]) {
                expect(fn('answer', { keywords })).toMatchObject(noEvidence);
            }
        }
        expect(strings.contains('answer', { keyword: '' })).toMatchObject(noEvidence);
        expect(strings.startsWith('answer', { prefix: '' })).toMatchObject(noEvidence);
        expect(strings.endsWith('answer', { suffix: '' })).toMatchObject(noEvidence);
        expect(strings.equals('', { expected: '' })).toMatchObject(noEvidence);
    });
    test('R4-B06 canonical string options and fixed diagnostics', () => {
        expect(() => strings.regex('test', {} as any)).toThrow('Regex pattern is required');
        expect(result(() => strings.regex(text, { pattern: '[' }))).toMatchObject(invalid);
        expect(strings.regex(text, { pattern: 'Paris', flags: null } as any)).toMatchObject(invalid);
        expect(strings.contains(text, { keyword: 'Paris', caseSensitive: 1 } as any)).toMatchObject(invalid);
        expect(strings.equals(text, { expected: text, trim: null } as any)).toMatchObject(invalid);
        expect(strings.lengthLessThan(text, { maxLength: Infinity })).toMatchObject(invalid);
        expect(strings.lengthGreaterThan(text, { minLength: '0' } as any)).toMatchObject(invalid);
        expect(strings.lengthBetween(text, { minLength: 100, maxLength: 1 })).toMatchObject(invalid);
        expect(strings.regex('safe', { pattern: '^private-pattern$' }).reason).not.toContain('private-pattern');
        expect(strings.oneLine('')).toMatchObject(noEvidence);
        expect(strings.oneLine('plain text').passed).toBe(true);
        expect(strings.oneLine({ includes: () => false } as any)).toMatchObject(noEvidence);
    });
    test('R4-B07 direct context precision needs tokens and a positive similarity threshold', () => {
        expect(contextPrecision({ query: '!!!', response: '', context: ['???'] },
            { threshold: 0, similarityThreshold: 0 }).passed).toBe(false);
        expect(contextPrecision({ query: '!!!', response: '', context: ['???'] },
            { threshold: 0 })).toMatchObject(noEvidence);
        expect(contextPrecision({ ...rag, ground_truth_contexts: ['!!!'] }, { threshold: 0 })).toMatchObject(noEvidence);
        expect(contextPrecision({ ...rag, ground_truth_contexts: [] }, { threshold: 0 })).toMatchObject(noEvidence);
        expect(contextPrecision(rag).passed).toBe(true);
        expect(contextPrecision({ ...rag, query: '', ground_truth_contexts: [text] }).passed).toBe(true);
        expect(contextPrecision({ ...rag, context: ['', text] })).toMatchObject({ score: 0.5, relevantIndices: [1], chunkScores: [0, 1] });
    });
    test('R4-B08 context precision uses one input/config snapshot', () => {
        const input = once({ ...rag });
        const config = once({ threshold: 0.5, similarityThreshold: 0.3 });
        expect(result(() => contextPrecision(input.value, config.value)).passed).toBe(true);
        expect(result(() => contextPrecision(throwing('context')))).toMatchObject(invalid);
        expect(result(() => contextPrecision(rag, throwing('threshold')))).toMatchObject(invalid);
    });
    test.each(defaultCases)('R4-B09 %s rejects non-record config without losing omitted defaults', (_name, fn, input) => {
        for (const config of malformed) expect(result(() => fn(input, config))).toMatchObject(invalid);
        expect(fn(input).passed).toBe(true);
        expect(fn(input, undefined).passed).toBe(true);
        expect(fn(input, Object.create(null)).passed).toBe(true);
    });
    test.each(defaultCases)('R4-B10 %s captures genuine inputs/config once and fixes getter errors', (_name, fn, input) => {
        expect(result(() => fn(once({ ...input }).value, once({ threshold: 0.5 }).value)).passed).toBe(true);
        expect(result(() => fn(input, throwing('threshold')))).toMatchObject(invalid);
        expect(result(() => fn(throwing(Object.keys(input)[0])))).toMatchObject(invalid);
        for (const config of [{ threshold: null }, { threshold: NaN }, { threshold: Infinity }]) {
            expect(result(() => fn(input, config))).toMatchObject(invalid);
        }
    });
    test('R4-B11 JSON config shape is distinct from legitimate JSON primitive payloads', () => {
        for (const config of malformed) expect(result(() => jsonSchema('true', config as any))).toMatchObject(invalid);
        for (const response of ['true', 'false', 'null', '0', '"text"', '[]', '{}']) {
            expect(isJson(response).passed).toBe(true);
            expect(containsJson(response).passed).toBe(true);
        }
        const config = once({ schema: { type: 'boolean' } });
        expect(jsonSchema('true', config.value).passed).toBe(true);
        expect(jsonSchema('true', { schema: {} })).toMatchObject({ passed: false, reason: 'SCHEMA_MISSING' });
        expect(jsonSchema('true', throwing('schema'))).toMatchObject({ passed: false, reason: 'SCHEMA_UNSUPPORTED' });
    });
    test('R4-B12 dispatch validates even configurations ignored by a leaf', () => {
        for (const config of malformed) expect(runMetric('one_line', 'answer', config as any)).toMatchObject(invalid);
        expect(runMetric('one_line', 'answer').passed).toBe(true);
        expect(runMetric('is_json', 'false').passed).toBe(true);
        const config = once({ pattern: '^Paris' });
        expect(runMetric('REGEX', text, config.value).passed).toBe(true);
        expect(runMetric('one_line', 'answer', throwing('unused'))).toMatchObject(invalid);
        expect(() => getMetric({ toLowerCase: () => 'one_line' } as any)).toThrow('METRIC_NAME_INVALID');
        expect(() => hasMetric(null as any)).toThrow('METRIC_NAME_INVALID');
        expect(() => runMetric('unknown-private-marker', 'text')).toThrow('METRIC_UNKNOWN');
        expect(getMetric('REGEX')?.name).toBe('regex');
        expect(hasMetric('ONE_LINE')).toBe(true);
        expect(getAvailableMetrics()).toContain('one_line');
    });
    test('R4-B13 analyzer captures nested evidence and refuses hostile/invalid input', () => {
        const input = once({ response: text, context: once([text]).value });
        expect(analyzeContent(input.value).hasContext).toBe(true);
        expect(() => analyzeContent(throwing('response'))).toThrow('EVALUATION_INPUT_INVALID');
        expect(() => analyzeContent({ response: text, reference: true } as any)).toThrow('EVALUATION_INPUT_INVALID');
        expect(analyzeContent({ response: text, context: [' ', ''] }).hasContext).toBe(false);
        expect(analyzeContent({ response: text }).inputType).toBe('text');
    });
    test('R4-B14 characteristic descriptions capture once and validate the record', () => {
        const chars = analyzeContent({ response: 'const total = 1;' });
        expect(result(() => describeCharacteristics(once(chars).value))).toContain('Code language:');
        expect(() => describeCharacteristics(throwing('inputType'))).toThrow('EVALUATION_INPUT_INVALID');
        expect(() => describeCharacteristics({ ...chars, hasCode: 'yes' } as any)).toThrow('EVALUATION_INPUT_INVALID');
    });
    test('R4-B15 selection rejects config shapes and preserves omitted defaults', () => {
        const input = { response: '{"valid":true}' };
        const chars = analyzeContent(input);
        for (const config of malformed) expect(() => selectMetrics(chars, input, config as any)).toThrow('EVALUATION_CONFIG_INVALID');
        for (const config of [{ minConfidence: NaN }, { maxMetrics: 0.5 }, { maxMetrics: null },
            { enableSecurityChecks: 1 }, { includeCategories: ['unknown'] }]) {
            expect(() => selectMetrics(chars, input, config as any)).toThrow('EVALUATION_CONFIG_INVALID');
        }
        expect(selectMetrics(chars, input).some(metric => metric.name === 'is_json')).toBe(true);
        expect(selectMetrics(chars, input, { maxMetrics: 0 })).toEqual([]);
    });
    test('R4-B16 selection captures characteristics, input and config once', () => {
        const input = { response: text, query: text, context: [text], reference: text };
        const chars = analyzeContent(input);
        expect(result(() => selectMetrics(once(chars).value, once(input).value, once({ minConfidence: 0.5 }).value)).length).toBeGreaterThan(0);
        expect(() => selectMetrics(throwing('hasJson'), input)).toThrow('EVALUATION_CONFIG_INVALID');
        expect(() => selectMetrics(chars, input, throwing('minConfidence'))).toThrow('EVALUATION_CONFIG_INVALID');
        expect(selectMetrics(chars, { response: '' }, { minConfidence: 0 })).toEqual([]);
    });
    test('R4-B17 metric config builder does not reread caller fields', () => {
        const metric = { name: 'one_line', category: 'string' as const, confidence: 0.6, reason: 'selected' };
        const input = once({ response: text, query: text });
        expect(result(() => buildMetricConfig(once(metric).value, input.value))).toMatchObject({ response: text, query: text });
        expect(() => buildMetricConfig(metric, throwing('response'))).toThrow('EVALUATION_INPUT_INVALID');
        expect(() => buildMetricConfig({ ...metric, confidence: NaN }, { response: text })).toThrow('EVALUATION_CONFIG_INVALID');
        expect(buildMetricConfig({ ...metric, name: 'length_between', category: 'quality' }, { response: text }))
            .toMatchObject({ minLength: 10, maxLength: 5000 });
    });
    test('R4-B20 direct recall selection/config mapping requires genuine reference evidence', () => {
        const input = { response: text, query: text, context: [text] };
        const chars = analyzeContent(input);
        expect(selectMetrics(chars, input).some(metric => metric.name === 'context_recall')).toBe(false);
        const withReference = { ...input, reference: text };
        const selected = selectMetrics(analyzeContent(withReference), withReference);
        const recall = selected.find(metric => metric.name === 'context_recall');
        expect(recall).toBeDefined();
        if (recall) {
            const config = buildMetricConfig(recall, withReference);
            expect(config.expected_response).toBe(text);
            expect(runMetric(recall.name, text, config).passed).toBe(true);
        }
    });
    test('R4-B18 recommended metric names cannot resolve inherited object properties', () => {
        expect(getRecommendedMetrics('__proto__')).toEqual(['one_line']);
        expect(getRecommendedMetrics('constructor')).toEqual(['one_line']);
        expect(getRecommendedMetrics('json')).toEqual(['is_json', 'contains_json']);
        expect(() => getRecommendedMetrics({ toString: () => 'json' } as any)).toThrow('EVALUATION_INPUT_INVALID');
    });
});

// Explicit additional C5 manifest; this does not claim all package exports.
const contracts: Array<[string, string, string[]]> = [
    ...stringCases.map(([name]) => ['metrics/string.ts', name, ['response', 'config']] as [string, string, string[]]),
    ['metrics/rag/context-precision.ts', 'contextPrecision', ['input', 'config']],
    ['metrics/index.ts', 'runMetric', ['config']],
    ['autoeval/analyzer.ts', 'analyzeContent', ['input']],
    ['autoeval/analyzer.ts', 'describeCharacteristics', ['chars']],
    ['autoeval/selector.ts', 'selectMetrics', ['chars', 'input', 'config']],
    ['autoeval/selector.ts', 'buildMetricConfig', ['metric', 'input']],
];
function identifiers(node: ts.Node, name: string): ts.Identifier[] {
    const result: ts.Identifier[] = [];
    const visit = (child: ts.Node) => {
        const parent = child.parent;
        const propertyName = parent && ((ts.isPropertyAccessExpression(parent) && parent.name === child) ||
            (ts.isPropertyAssignment(parent) && parent.name === child));
        if (ts.isIdentifier(child) && child.text === name && !propertyName) result.push(child);
        ts.forEachChild(child, visit);
    };
    visit(node); return result;
}
test.each(contracts)('R4-B19-AST %s %s rebinds before downstream reads', (file, name, parameters) => {
    const source = ts.createSourceFile(file, readFileSync(resolve(__dirname, '..', file), 'utf8'), ts.ScriptTarget.Latest, true);
    const fn = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name) as ts.FunctionDeclaration | undefined;
    expect(fn?.body).toBeDefined();
    if (!fn?.body) return;
    for (const parameter of parameters) {
        const captures: ts.CallExpression[] = [];
        const visit = (node: ts.Node) => {
            if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'snapshotBoundary' &&
                node.arguments.some(argument => identifiers(argument, parameter).length > 0)) captures.push(node);
            ts.forEachChild(node, visit);
        };
        visit(fn.body);
        expect(captures).toHaveLength(1);
        const capture = captures[0];
        if (!capture) continue;
        const assignment = capture.parent;
        const rebound = ts.isBinaryExpression(assignment) && assignment.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
            ((ts.isIdentifier(assignment.left) && assignment.left.text === parameter) ||
                (ts.isObjectLiteralExpression(assignment.left) && identifiers(assignment.left, parameter).length === 1));
        expect(rebound).toBe(true);
        const violations = identifiers(fn.body, parameter).filter(read => {
            if (read.pos >= capture.pos && read.end <= capture.end) return false;
            if (rebound && ts.isBinaryExpression(assignment) && read.pos >= assignment.left.pos && read.end <= assignment.left.end) return false;
            return !(rebound && read.pos >= assignment.end);
        });
        expect(violations.map(node => source.getLineAndCharacterOfPosition(node.getStart()).line + 1)).toEqual([]);
    }
});
