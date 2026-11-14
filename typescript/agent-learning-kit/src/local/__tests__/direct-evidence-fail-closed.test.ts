import { faithfulness } from '../metrics/rag/faithfulness.js';
import { hallucinationDetection } from '../metrics/hallucination/detection.js';
import { bleuScore, rougeScore, recallScore, levenshteinSimilarity, numericSimilarity, semanticListContains } from '../metrics/similarity.js';

import * as ragHelpers from '../metrics/rag/types.js';
import * as hallucinationHelpers from '../metrics/hallucination/types.js';
afterEach(() => jest.restoreAllMocks());

const evidence = { query: 'What is the capital?', response: 'Paris is the capital of France.', context: 'Paris is the capital of France.' };

describe('W04 r2 direct evidence', () => {
    test('R2-D07 nonfinite evidence scores cannot be counted as supported claims', () => {
        jest.spyOn(ragHelpers, 'jaccardSimilarity').mockReturnValue(Infinity);
        jest.spyOn(hallucinationHelpers, 'calculateOverlap').mockReturnValue(Infinity);
        expect(faithfulness(evidence, { threshold: 0 })).toMatchObject({ score: 0, passed: false, reason: 'METRIC_RESULT_INVALID' });
        expect(hallucinationDetection(evidence, { threshold: 0 })).toMatchObject({ score: 0, passed: false, reason: 'METRIC_RESULT_INVALID' });
    });
    test('R2-D01 faithfulness zero claims and missing evidence fail, real claims pass', () => {
        for (const response of ['...', '', '   ']) {
            expect(faithfulness({ ...evidence, response }, { threshold: 0 })).toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
        }
        expect(faithfulness({ ...evidence, context: [] }, { threshold: 0 }).passed).toBe(false);
        expect(faithfulness(evidence).passed).toBe(true);
    });
    test('R2-D02 hallucination zero factual claims never passes including disabled details', () => {
        for (const response of ['', '...', 'I think this is a pleasant answer.']) {
            expect(hallucinationDetection({ ...evidence, response }, { threshold: 0, includeClaimAnalysis: false }))
                .toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE', supportedClaims: 0, unsupportedClaims: 0 });
        }
        expect(hallucinationDetection({ ...evidence, context: '' }).passed).toBe(false);
        expect(hallucinationDetection(evidence).passed).toBe(true);
    });
    test('R2-D03 similarity empty references and empty token sets never pass', () => {
        expect(recallScore('answer', { reference: '' })).toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
        expect(levenshteinSimilarity('', { reference: '' }).passed).toBe(false);
        for (const run of [bleuScore, rougeScore, recallScore, levenshteinSimilarity]) {
            expect(run('   ', { reference: '   ' })).toMatchObject({ score: 0, passed: false });
            expect(run('the cat sat on the mat', { reference: 'the cat sat on the mat' }).passed).toBe(true);
        }
    });
    test('R2-D04 numeric nonfinite values and diagnostics are rejected without coercion', () => {
        for (const config of [{ reference: Infinity }, { reference: NaN }, { reference: new Number(1) }, { reference: 1, tolerance: Infinity }]) {
            expect(numericSimilarity('1', config as any)).toMatchObject({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
        }
        expect(numericSimilarity('Infinity', { reference: 1 })).toMatchObject({ score: 0, passed: false });
        expect(numericSimilarity('PRIVATE_SENTINEL', { reference: 1 }).reason).toBe('NUMERIC_INPUT_INVALID');
        expect(numericSimilarity('1', { reference: 1, tolerance: 0 }).passed).toBe(true);
    });
    test('R2-D05 list snapshot has a nonzero denominator and fixed diagnostics', () => {
        expect(semanticListContains('text', { items: [] })).toMatchObject({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
        const items = (function* () { yield 'apple'; yield 'PRIVATE_SENTINEL'; })();
        const result = semanticListContains('apple', { items: items as any, threshold: 1 });
        expect(result.score).toBe(0.5);
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('CHECK_FAILED');
        expect(semanticListContains('apple', { items: ['apple'] }).passed).toBe(true);
    });
    test('R2-D06 BLEU invalid weights cannot manufacture a numeric score', () => {
        for (const ngramWeights of [[Infinity], [NaN], [-1], [], [new Number(1)]]) {
            expect(bleuScore('the cat', { reference: 'the cat', ngramWeights } as any))
                .toMatchObject({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
        }
        expect(bleuScore('the cat', { reference: 'the cat', ngramWeights: [1] }).passed).toBe(true);
    });
});
