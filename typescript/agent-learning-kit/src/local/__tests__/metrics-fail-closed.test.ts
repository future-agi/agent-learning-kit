import { sqlInjection } from '../metrics/code/sql-injection.js';
import { xssDetection } from '../metrics/code/xss-detection.js';
import { secretsDetection } from '../metrics/code/secrets-detection.js';
import { jsonSchema } from '../metrics/json.js';
import { answerRelevance } from '../metrics/rag/answer-relevance.js';
import { contextRecall } from '../metrics/rag/context-recall.js';
import { contextRelevance, contextUtilization } from '../metrics/rag/context-relevance.js';

const security = [
    { id: 'FO-10', run: sqlInjection },
    { id: 'FO-11', run: xssDetection },
    { id: 'FO-12', run: secretsDetection },
];

describe.each(security)('$id security fail-closed', ({ run }) => {
    test('malformed requested regex cannot be skipped even with threshold zero', () => {
        const result = run({ code: 'const value = 1;' }, { customPatterns: ['['], threshold: 0, minSeverity: 'critical' });
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('INVALID_SECURITY_PATTERN');
    });
    test('empty code is no evidence', () => {
        expect(run({ code: '' }).passed).toBe(false);
    });
    test('genuine PASS control with custom check executed', () => {
        expect(run({ code: 'const value = 1;' }, { customPatterns: ['dangerous_call'] }).passed).toBe(true);
    });
});

describe('FO-13 schema fail-closed', () => {
    test.each([undefined, null, false, {}, { unknownKeyword: true }, { properties: { absent: { pattern: '[' } } }])('invalid/absent/unsupported schema %p', schema => {
        const result = jsonSchema('{}', { schema } as any);
        expect(result.passed).toBe(false);
        expect(result.score).toBe(0);
    });
    test('false nested schema rejects a present property', () => {
        expect(jsonSchema('{"x":1}', { schema: { properties: { x: false } } }).passed).toBe(false);
    });
    test('diagnostics omit values and raw parser errors', () => {
        const result = jsonSchema('"PRIVATE_SENTINEL"', { schema: { type: 'string', enum: ['allowed'] } });
        expect(result.passed).toBe(false);
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
    });
    test('R2-J02 schema fields are snapshotted before validation', () => {
        let reads = 0;
        const schema = { get type() { reads++; return 'number'; } };
        expect(jsonSchema('1', { schema }).passed).toBe(true);
        expect(reads).toBe(1);
    });
    test('R2-J01 schema tokens and numeric bounds cannot be boxed', () => {
        expect(jsonSchema('1', { schema: { type: new String('number') } }).passed).toBe(false);
        expect(jsonSchema('1', { schema: { minimum: new Number(0) } }).passed).toBe(false);
        expect(jsonSchema('1', { schema: { type: 'number', minimum: 0 } }).passed).toBe(true);
    });
    test('genuine supported-schema PASS control', () => {
        expect(jsonSchema('{"x":1}', { schema: { type: 'object', properties: { x: { type: 'integer', minimum: 1 } }, required: ['x'], additionalProperties: false } }).passed).toBe(true);
    });
});

describe('W04 absent RAG evidence', () => {
    const input = { query: 'machine learning', response: 'machine learning', context: ['machine learning'] };
    test('missing query never passes relevance', () => {
        expect(answerRelevance({ ...input, query: '' }, { threshold: 0 }).passed).toBe(false);
        expect(contextRelevance({ ...input, query: '' }, { threshold: 0 }).passed).toBe(false);
    });
    test.each([{}, { expected_response: '...' }, { ground_truth_contexts: ['   '] }])('missing usable references %p', extra => {
        expect(contextRecall({ ...input, ...extra }, { threshold: 0 }).passed).toBe(false);
    });
    test('missing context cannot pass at threshold zero', () => {
        expect(contextRecall({ ...input, context: [], expected_response: 'machine learning' }, { threshold: 0 }).passed).toBe(false);
        expect(contextRelevance({ ...input, context: ['   '] }, { threshold: 0 }).passed).toBe(false);
        expect(contextUtilization({ ...input, context: ['   '] }, { threshold: 0 }).passed).toBe(false);
    });
    test('genuine evidence PASS controls', () => {
        expect(answerRelevance(input).passed).toBe(true);
        expect(contextRelevance(input).passed).toBe(true);
        expect(contextRecall({ ...input, expected_response: 'machine learning' }).passed).toBe(true);
        expect(contextUtilization(input).passed).toBe(true);
    });
});
