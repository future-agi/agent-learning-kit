import {
    bleuScore,
    rougeScore,
    recallScore,
    levenshteinSimilarity,
    numericSimilarity,
    semanticListContains
} from '../metrics/similarity';

describe('Round 4 BLEU positive-weight membership', () => {
    it('R4-BLEU-01 keeps a positive bigram order when normalization underflows', () => {
        const result = bleuScore('alpha', {
            reference: 'alpha',
            ngramWeights: [Number.MAX_VALUE, Number.MIN_VALUE]
        });

        expect(result.score).toBe(0);
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('CHECK_FAILED');
    });

    it('R4-BLEU-02 keeps a later positive order among intentionally zero weights', () => {
        const result = bleuScore('alpha beta gamma', {
            reference: 'alpha beta gamma',
            ngramWeights: [Number.MAX_VALUE, 0, 0, Number.MIN_VALUE]
        });

        expect(result.score).toBe(0);
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('CHECK_FAILED');
    });

    it('R4-BLEU-03 preserves explicitly sparse unigram weights', () => {
        const result = bleuScore('alpha', {
            reference: 'alpha',
            ngramWeights: [1, 0, 0, 0]
        });

        expect(result.score).toBe(1);
        expect(result.passed).toBe(true);
    });

    it('R4-BLEU-04 accepts extreme finite weights when all required orders have evidence', () => {
        const result = bleuScore('alpha beta', {
            reference: 'alpha beta',
            ngramWeights: [Number.MAX_VALUE, Number.MIN_VALUE]
        });

        expect(result.score).toBe(1);
        expect(result.passed).toBe(true);
    });

    it('R4-BLEU-05 retains an ordinary positive zero-precision order without smoothing', () => {
        const result = bleuScore('alpha', {
            reference: 'alpha',
            ngramWeights: [1, 1]
        });

        expect(result.score).toBe(0);
        expect(result.passed).toBe(false);
    });

    it('R4-BLEU-06 accepts positive subnormal weights when every precision is positive', () => {
        const result = bleuScore('alpha beta', {
            reference: 'alpha beta',
            ngramWeights: [Number.MIN_VALUE, Number.MIN_VALUE]
        });

        expect(result.score).toBe(1);
        expect(result.passed).toBe(true);
    });

    it('R4-BLEU-07 preserves the brevity penalty with sparse weights', () => {
        const result = bleuScore('alpha', {
            reference: 'alpha beta',
            ngramWeights: [1, 0, 0, 0]
        });

        expect(result.score).toBeCloseTo(Math.exp(-1), 12);
        expect(result.passed).toBe(false);
    });

    it('R4-BLEU-08 snapshots the declared weights once before preserving membership', () => {
        let reads = 0;
        const result = bleuScore('alpha', {
            reference: 'alpha',
            get ngramWeights() {
                reads++;
                return reads === 1 ? [Number.MAX_VALUE, Number.MIN_VALUE] : [1, 0];
            }
        });

        expect(reads).toBe(1);
        expect(result.score).toBe(0);
        expect(result.passed).toBe(false);
    });

    it.each([
        { label: 'empty', weights: [] },
        { label: 'all zero', weights: [0, 0, 0, 0] },
        { label: 'negative', weights: [1, -1] },
        { label: 'NaN', weights: [1, NaN] },
        { label: 'positive infinity', weights: [1, Infinity] },
        { label: 'negative infinity', weights: [1, -Infinity] },
        { label: 'null component', weights: [1, null] },
        { label: 'string component', weights: [1, '1'] }
    ])('R4-BLEU-09 rejects $label weights', ({ weights }) => {
        const result = bleuScore('alpha beta', {
            reference: 'alpha beta',
            ngramWeights: weights as any
        });

        expect(result).toEqual({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });
});

const text = 'alpha beta gamma delta';
const metrics: Array<{
    name: string;
    run: (config: any) => ReturnType<typeof bleuScore>;
    validConfig: Record<string, unknown>;
}> = [
    { name: 'bleuScore', run: config => bleuScore(text, config), validConfig: { reference: text } },
    { name: 'rougeScore', run: config => rougeScore(text, config), validConfig: { reference: text } },
    { name: 'recallScore', run: config => recallScore(text, config), validConfig: { reference: text } },
    { name: 'levenshteinSimilarity', run: config => levenshteinSimilarity(text, config), validConfig: { reference: text } },
    { name: 'numericSimilarity', run: config => numericSimilarity('42', config), validConfig: { reference: 42 } },
    { name: 'semanticListContains', run: config => semanticListContains(text, config), validConfig: { items: ['alpha'] } }
];

const nonRecords: Array<{
    label: string;
    make: (fields: Record<string, unknown>) => unknown;
}> = [
    { label: 'true', make: () => true },
    { label: 'false', make: () => false },
    { label: 'number', make: () => 1 },
    { label: 'string', make: () => 'config' },
    { label: 'null', make: () => null },
    { label: 'undefined required config', make: () => undefined },
    { label: 'empty array', make: () => [] },
    { label: 'array with valid fields', make: fields => Object.assign([], fields) },
    { label: 'non-plain object with valid fields', make: fields => Object.assign(Object.create({ inherited: true }), fields) },
    { label: 'boxed boolean with valid fields', make: fields => Object.assign(Object(true), fields) },
    { label: 'function with valid fields', make: fields => Object.assign(() => undefined, fields) }
];

describe.each(metrics)('Round 4 similarity configuration: $name', ({ run, validConfig }) => {
    it.each(nonRecords)('R4-SIM-01 rejects $label instead of applying defaults', ({ make }) => {
        const config = make(validConfig);
        let result: ReturnType<typeof bleuScore> | undefined;

        expect(() => { result = run(config); }).not.toThrow();
        expect(result).toEqual({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });

    it('R4-SIM-02 preserves genuine evidence with omitted optional fields', () => {
        const result = run(validConfig);

        expect(result.score).toBe(1);
        expect(result.passed).toBe(true);
    });

    it('R4-SIM-03 accepts a null-prototype configuration record', () => {
        const result = run(Object.assign(Object.create(null), validConfig));

        expect(result.score).toBe(1);
        expect(result.passed).toBe(true);
    });

    it('R4-SIM-04 rejects a throwing own getter with a fixed diagnostic', () => {
        const config = Object.defineProperty({ ...validConfig }, 'extra', {
            get() { throw new Error('untrusted configuration getter'); }
        });
        let result: ReturnType<typeof bleuScore> | undefined;

        expect(() => { result = run(config); }).not.toThrow();
        expect(result).toEqual({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });
});
