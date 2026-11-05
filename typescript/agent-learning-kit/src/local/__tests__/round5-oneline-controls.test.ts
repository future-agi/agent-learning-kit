import { oneLine } from '../metrics/string';

const noEvidence = { score: 0, passed: false, reason: 'NO_EVIDENCE' };

describe('oneLine documented no-evidence contract', () => {
    it.each([
        ['empty string', ''],
        ['spaces only', '   '],
        ['tab only', '\t'],
        ['whitespace with a newline', ' \n '],
    ])('fails with NO_EVIDENCE for %s', (_name, value) => {
        expect(oneLine(value)).toEqual(noEvidence);
    });

    it.each([
        ['number', 123],
        ['null', null],
        ['undefined', undefined],
        ['array', ['one line']],
    ])('fails with NO_EVIDENCE for a non-string %s', (_name, value) => {
        expect(oneLine(value as unknown as string)).toEqual(noEvidence);
    });

    it('still passes genuine single-line text', () => {
        expect(oneLine('one line')).toEqual({ score: 1, passed: true, reason: 'SINGLE_LINE' });
    });

    it('still fails genuine multi-line text', () => {
        expect(oneLine('first\nsecond')).toEqual({ score: 0, passed: false, reason: 'MULTIPLE_LINES' });
    });
});
