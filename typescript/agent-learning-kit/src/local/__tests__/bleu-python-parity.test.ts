import { bleuScore } from '../metrics/similarity';

// Stdlib NLTK 3.9 sentence_bleu + method1 reproduction, not the TS implementation.
// BLEU-ORACLE-r1.json SHA-256: b9b0f863e30b1b8f3922e26c32a65392128f7de698b3c735d45e5878c1f39c77
// bleu_oracle.py SHA-256: 2b807b002e7eb9f55c5b65f535e24cb82e8b136f3615d592b8e6980eb87ad619
interface GoldenRow {
    name: string;
    hypothesis: string;
    references: string[];
    weights: number[];
    expected_score: number;
}

const defaultWeights = [0.25, 0.25, 0.25, 0.25];
const pythonWhitespace = '\t\n\u000b\f\r\u001c\u001d\u001e\u001f \u0085                 　';
const goldenRows: GoldenRow[] = [
    { name: 'integration pair', hypothesis: 'The cat is sitting on the mat', references: ['The cat sat on the mat'], weights: defaultWeights, expected_score: 0.20556680845025987 },
    { name: 'identical', hypothesis: 'the cat sat on the mat', references: ['the cat sat on the mat'], weights: defaultWeights, expected_score: 1.0 },
    { name: 'no overlap', hypothesis: 'hello world', references: ['goodbye moon'], weights: defaultWeights, expected_score: 0.0 },
    { name: 'short paraphrase', hypothesis: 'the cat sleeps', references: ['the cat naps'], weights: defaultWeights, expected_score: 0.24028114141347542 },
    { name: 'hypothesis longer', hypothesis: 'the cat sat on the mat today', references: ['the cat sat on the mat'], weights: defaultWeights, expected_score: 0.8091067115702212 },
    { name: 'hypothesis shorter', hypothesis: 'the cat sat', references: ['the cat sat on the mat'], weights: defaultWeights, expected_score: 0.20687381245863395 },
    { name: 'multiple references', hypothesis: 'the cat sat on the mat', references: ['the cat is on the mat', 'a cat sat on a mat'], weights: defaultWeights, expected_score: 0.35930411196308426 },
    { name: 'case difference', hypothesis: 'The Cat sat on the mat', references: ['the cat sat on the mat'], weights: defaultWeights, expected_score: 0.5081327481546147 },
    { name: 'punctuation attached', hypothesis: 'the cat, sat on the mat.', references: ['the cat sat on the mat'], weights: defaultWeights, expected_score: 0.21711852081087685 },
    { name: 'custom unnormalized weights', hypothesis: 'alpha beta', references: ['alpha gamma'], weights: [1.0, 1.0], expected_score: 0.05000000000000001 },
    { name: 'closest reference tie prefers shorter', hypothesis: 'a b c d', references: ['a b c d e', 'a b c'], weights: defaultWeights, expected_score: 1.0 },
    { name: 'reference clipping uses maximum not sum', hypothesis: 'a a a a', references: ['a a b', 'a b'], weights: [1.0], expected_score: 0.5 },
    { name: 'reference clipping repeated bigrams', hypothesis: 'a a a a', references: ['a a a b', 'a a b b'], weights: [0.5, 0.5], expected_score: 0.7071067811865475 },
    { name: 'single token identical', hypothesis: 'alpha', references: ['alpha'], weights: defaultWeights, expected_score: 0.1778279410038923 },
    { name: 'sparse unigram weights', hypothesis: 'alpha', references: ['alpha'], weights: [1.0, 0.0, 0.0, 0.0], expected_score: 1.0 },
    { name: 'zero unigram weight still needs unigram overlap', hypothesis: 'alpha beta', references: ['gamma delta'], weights: [0.0, 1.0], expected_score: 0.0 },
    { name: 'all case differs', hypothesis: 'THE CAT', references: ['the cat'], weights: defaultWeights, expected_score: 0.0 },
    { name: 'punctuation-only token', hypothesis: '!!!', references: ['!!!'], weights: defaultWeights, expected_score: 0.1778279410038923 },
    { name: 'Python whitespace', hypothesis: pythonWhitespace + 'alpha' + pythonWhitespace + 'beta gamma delta' + pythonWhitespace, references: ['alpha beta gamma delta'], weights: defaultWeights, expected_score: 1.0 },
    { name: 'BOM is not Python whitespace', hypothesis: '﻿alpha beta gamma delta', references: ['alpha beta gamma delta'], weights: defaultWeights, expected_score: 0.3976353643835253 },
    { name: 'BOM-only token', hypothesis: '﻿', references: ['﻿'], weights: defaultWeights, expected_score: 0.1778279410038923 },
    { name: 'zero-width space is not whitespace', hypothesis: 'alpha​beta gamma delta', references: ['alpha beta gamma delta'], weights: defaultWeights, expected_score: 0.17216896116316355 },
    { name: 'legacy missing four-gram', hypothesis: 'the cat sat on the mat', references: ['the cat is on the mat'], weights: defaultWeights, expected_score: 0.25406637407730737 },
    { name: 'positive subnormal smoothing weight', hypothesis: 'alpha', references: ['alpha'], weights: [Number.MAX_VALUE, Number.MIN_VALUE], expected_score: 1.0 },
    { name: 'ordinary unnormalized smoothing weights', hypothesis: 'alpha', references: ['alpha'], weights: [1.0, 1.0], expected_score: 0.10000000000000002 },
    { name: 'custom five orders', hypothesis: 'a b c d e f', references: ['a b c d e g'], weights: [0.1, 0.2, 0.3, 0.2, 0.2], expected_score: 0.6915028921812392 }
];

describe('BLEU Python sentence-level parity', () => {
    it.each(goldenRows)('$name', ({ hypothesis, references, weights, expected_score }) => {
        const referenceInputs: Array<string | string[]> = [references];
        if (references.length === 1) referenceInputs.push(references[0]!);
        for (const reference of referenceInputs) {
            const configurations: Array<{ reference: string | string[]; ngramWeights?: number[] }> = [
                { reference, ngramWeights: weights }
            ];
            if (weights.length === 4 && weights.every(weight => weight === 0.25)) {
                configurations.push({ reference });
            }
            for (const config of configurations) {
                const result = bleuScore(hypothesis, config);
                if (expected_score === 0) expect(result.score).toBe(0);
                else expect(Math.abs(result.score - expected_score)).toBeLessThan(1e-4);
                expect(result.passed).toBe(expected_score >= 0.5);
                expect(result.reason).toBe(`BLEU score: ${result.score.toFixed(4)}`);
            }
        }
    });

    it.each(Array.from(pythonWhitespace))('splits on Python whitespace U+%s', separator => {
        const text = ['alpha', 'beta', 'gamma', 'delta'].join(separator);
        expect(bleuScore(text, { reference: 'alpha beta gamma delta' }).score).toBe(1);
        expect(bleuScore('alpha beta gamma delta', { reference: [text] }).score).toBe(1);
    });

    it.each([
        { label: 'empty', response: '' },
        { label: 'whitespace', response: pythonWhitespace },
        { label: 'null', response: null },
        { label: 'undefined', response: undefined },
        { label: 'number', response: 42 },
        { label: 'object', response: {} },
        { label: 'array', response: ['alpha'] }
    ])('rejects $label response', ({ response }) => {
        expect(bleuScore(response as any, { reference: 'alpha' }))
            .toEqual({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
    });

    it.each([
        { label: 'missing', reference: undefined },
        { label: 'null', reference: null },
        { label: 'number', reference: 42 },
        { label: 'object', reference: {} },
        { label: 'empty string', reference: '' },
        { label: 'whitespace string', reference: pythonWhitespace },
        { label: 'empty array', reference: [] },
        { label: 'empty array member', reference: ['alpha', ''] },
        { label: 'whitespace array member', reference: ['alpha', pythonWhitespace] },
        { label: 'non-string array member', reference: ['alpha', 42] },
        { label: 'null array member', reference: ['alpha', null] },
        { label: 'nested array member', reference: ['alpha', ['alpha']] },
        { label: 'sparse array', reference: new Array(1) }
    ])('rejects $label reference without dropping invalid evidence', ({ reference }) => {
        expect(bleuScore('alpha', { reference } as any))
            .toEqual({ score: 0, passed: false, reason: 'NO_EVIDENCE' });
    });

    it('snapshots the reference array and its accessor slots once', () => {
        let referenceReads = 0;
        let slotReads = 0;
        const references = Object.defineProperty([''], '0', {
            get() {
                slotReads++;
                if (slotReads > 1) throw new Error('reference slot reread');
                return 'alpha beta gamma delta';
            }
        });
        const result = bleuScore('alpha beta gamma delta', {
            get reference() {
                referenceReads++;
                if (referenceReads > 1) throw new Error('reference reread');
                return references;
            }
        });
        expect(result).toEqual({ score: 1, passed: true, reason: 'BLEU score: 1.0000' });
        expect(referenceReads).toBe(1);
        expect(slotReads).toBe(1);
    });

    it('retains the fixed failure diagnostic for throwing reference slots', () => {
        const references = Object.defineProperty([''], '0', {
            get() { throw new Error('untrusted reference getter'); }
        });
        expect(bleuScore('alpha', { reference: references }))
            .toEqual({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });
});
