import { runMetric } from '../metrics';
import { bleuScore } from '../metrics/similarity';

const text = 'alpha beta gamma delta';
const invalidReferenceResult = { score: 0, passed: false, reason: 'NO_EVIDENCE' };
const perfectMatchResult = { score: 1, passed: true, reason: 'BLEU score: 1.0000' };

interface ReferenceCase {
    label: string;
    makeReference: (response: string) => unknown;
    response?: string;
    ngramWeights?: number[];
}

const invalidReferences: ReferenceCase[] = [
    { label: 'Set', makeReference: response => new Set([response]) },
    {
        label: 'iterable array-like with a non-string indexed member',
        makeReference: response => ({
            0: 42,
            length: 1,
            *[Symbol.iterator]() { yield response; }
        })
    },
    {
        label: 'generator object',
        makeReference: response => (function* () { yield response; })()
    },
    { label: 'Map', makeReference: response => new Map([[response, response]]) },
    {
        label: 'boxed String',
        // A single character and unigram weight make the boxed string's native
        // iterator yield a perfect match, exposing acceptance rather than a low score.
        response: 'a',
        ngramWeights: [1],
        makeReference: response => new String(response)
    },
    { label: 'array containing a Set', makeReference: response => [new Set([response])] }
];

const validReferences: ReferenceCase[] = [
    { label: 'plain array', makeReference: response => [response] },
    { label: 'frozen array', makeReference: response => Object.freeze([response]) },
    { label: 'array Proxy', makeReference: response => new Proxy([response], {}) }
];

const entryPoints = [
    {
        label: 'bleuScore',
        run: (response: string, reference: unknown, ngramWeights?: number[]) =>
            bleuScore(response, { reference, ngramWeights } as any)
    },
    {
        label: 'runMetric',
        run: (response: string, reference: unknown, ngramWeights?: number[]) =>
            runMetric('bleu_score', response, { reference, ngramWeights } as any)
    }
];

describe.each(entryPoints)('$label reference container validation', ({ run }) => {
    it.each(invalidReferences)('rejects $label with the existing invalid-reference result', ({
        makeReference, response = text, ngramWeights
    }) => {
        // Construct a fresh value per entry point, especially for one-shot generators.
        expect(run(response, makeReference(response), ngramWeights)).toEqual(invalidReferenceResult);
    });

    it.each(validReferences)('accepts $label as a perfect match', ({ makeReference }) => {
        expect(run(text, makeReference(text))).toEqual(perfectMatchResult);
    });
});
