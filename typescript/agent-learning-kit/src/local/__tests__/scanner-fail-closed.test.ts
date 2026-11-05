import { Scanner } from '../scanner/scanner.js';
import { ScanRule } from '../scanner/types.js';

const rule = (id: string, check: ScanRule['check']): ScanRule => ({
    id, check, name: 'fixture', category: 'custom', severity: 'high',
    description: 'fixture', enabledByDefault: false,
});
afterEach(() => jest.restoreAllMocks());

describe('W04 scanner fail-closed', () => {
    test.each([{ enableRules: ['missing'] }, { enableRules: ['tag:missing'] }, { enableRules: [] }])('FO-03 unknown or zero selected rules %p', ({ enableRules }) => {
        const result = new Scanner({ enableRules }).scan('safe content');
        expect(result.passed).toBe(false);
        expect(result.summary).not.toContain('No issues found');
    });

    test('FO-03 all excluded rules are incomplete', () => {
        const scanner = new Scanner();
        const result = scanner.scan('safe content', { disableRules: scanner.getAllRules().map(r => r.id) });
        expect(result.passed).toBe(false);
    });

    test.each([false, true])('FO-03 throwing rule retained alongside pass=%p', mixed => {
        const log = jest.spyOn(console, 'error').mockImplementation(() => {});
        const customRules = [rule('broken', () => { throw new Error('PRIVATE_SENTINEL'); }), rule('ok', () => [])];
        const result = new Scanner({ customRules, enableRules: mixed ? ['ok', 'broken'] : ['broken'] }).scan('safe');
        expect(result.passed).toBe(false);
        expect((result as any).coverage.errored).toBe(1);
        expect(JSON.stringify([result, log.mock.calls])).not.toContain('PRIVATE_SENTINEL');
    });

    test('FO-03 malformed rule output cannot pass', () => {
        const result = new Scanner({ customRules: [rule('broken', () => null as any)], enableRules: ['broken'] }).scan('safe');
        expect(result.passed).toBe(false);
    });

    test('FO-03 missing RAG evidence cannot be mistaken for no findings', () => {
        const result = new Scanner({ enableRules: ['quality/hallucination'] }).scan('A claim.');
        expect(result.passed).toBe(false);
        expect((result as any).coverage.completed).toBe(0);
    });

    test('FO-03 genuine PASS control', () => {
        const result = new Scanner({ customRules: [rule('ok', () => [])], enableRules: ['ok'] }).scan('safe');
        expect(result.passed).toBe(true);
        expect(result.rulesExecuted).toEqual(['ok']);
    });
});
