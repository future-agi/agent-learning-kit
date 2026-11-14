import { Protect } from '../protect.js';
import { Evaluator } from '../evaluator.js';

const rules = [{ metric: 'Toxicity' }];
let request: jest.Mock;
let protect: Protect;
beforeEach(() => {
    jest.useFakeTimers();
    request = jest.fn();
    protect = new Protect({ evaluator: { request, baseUrl: 'https://fixture.invalid' } as unknown as Evaluator });
});
afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

describe('FO-04 protection fail-closed', () => {
    test.each([null, { eval_results: [] }, { eval_results: [null] }, { eval_results: [{}] }, { eval_results: [{ data: [], failure: false }] }])('empty/malformed result %p never passes', async response => {
        request.mockResolvedValue(response);
        const result = await protect.protect('input', rules, undefined, true);
        expect(result.status).not.toBe('passed');
        expect(result.reasons).not.toBe('All checks passed');
        expect(result.uncompleted_rules).toContain('Toxicity');
    });
    test('rejected transport cannot pass or leak', async () => {
        const log = jest.spyOn(console, 'error').mockImplementation(() => {});
        request.mockRejectedValue(new Error('PRIVATE_SENTINEL'));
        const result = await protect.protect('input', rules, undefined, true);
        expect(result.status).not.toBe('passed');
        expect(JSON.stringify([result, log.mock.calls])).not.toContain('PRIVATE_SENTINEL');
    });
    test('timeout leaves required coverage incomplete', async () => {
        request.mockReturnValue(new Promise(() => {}));
        const pending = protect.protect('input', rules, undefined, true, 10);
        await jest.advanceTimersByTimeAsync(11);
        const result = await pending;
        expect(result.status).not.toBe('passed');
        expect(result.uncompleted_rules).toEqual(['Toxicity']);
    });
    test('exhausted time budget never starts a check or passes', async () => {
        const result = await protect.protect('input', rules, undefined, true, 0);
        expect(result.status).not.toBe('passed');
        expect(request).not.toHaveBeenCalled();
    });
    test('mixed pass and rejected required rule stays incomplete', async () => {
        request.mockResolvedValueOnce({ eval_results: [{ data: ['Not Toxic'], failure: false }] }).mockRejectedValueOnce(new Error('fixture'));
        const result = await protect.protect('input', [...rules, { metric: 'Sexism' }], undefined, true);
        expect(result.status).not.toBe('passed');
        expect(result.completed_rules).toContain('Toxicity');
        expect(result.uncompleted_rules).toContain('Sexism');
    });
    test.each([{}, { failure: 'false' }, { failure: null }])('flash requires a boolean verdict %p', async row => {
        request.mockResolvedValue({ eval_results: [row] });
        expect((await protect.protect('input', null, undefined, true, 10, true)).status).not.toBe('passed');
    });
    test('R2-P01 failure diagnostics do not echo caller actions', async () => {
        request.mockResolvedValue({ eval_results: [{ data: ['Failed'], failure: true }] });
        for (const flash of [false, true]) {
            const result = await protect.protect('input', rules, 'PRIVATE_SENTINEL', true, 10, flash);
            expect(result.status).toBe('failed');
            expect(result.messages).toBe('PROTECTION_RULE_TRIGGERED');
            expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
        }
    });
    test('R2-P02 rule iterables are snapshotted once before validation', async () => {
        request.mockResolvedValue({ eval_results: [{ data: ['Not Toxic'], failure: false }] });
        const requested = (function* () { yield { metric: 'Toxicity' }; yield { metric: 'Sexism' }; })();
        await expect(protect.protect('input', requested as any)).resolves.toMatchObject({ status: 'passed', coverage: { required: 2, completed: 2 } });
        expect(request).toHaveBeenCalledTimes(2);
    });
    test('R2-P04 flash never ignores invalid pass flags or nonfinite scores', async () => {
        for (const row of [{ failure: false, passed: 'yes' }, { failure: false, output: Infinity }]) {
            request.mockResolvedValue({ eval_results: [row] });
            expect((await protect.protect('input', null, undefined, true, 10, true)).status).toBe('incomplete');
        }
    });
    test('R2-P03 boxed rule names reject before transport', async () => {
        request.mockResolvedValue({ eval_results: [{ data: ['Not Toxic'], failure: false }] });
        await expect(protect.protect('input', [{ metric: new String('Toxicity') }])).rejects.toThrow('PROTECT_RULE_UNKNOWN');
        expect(request).not.toHaveBeenCalled();
    });
    test('standard genuine PASS control', async () => {
        request.mockResolvedValue({ eval_results: [{ data: ['Not Toxic'], failure: false }] });
        const result = await protect.protect('input', rules, undefined, true);
        expect(result.status).toBe('passed');
        expect(result.completed_rules).toEqual(['Toxicity']);
        expect(result.uncompleted_rules).toEqual([]);
    });
    test('flash genuine PASS control', async () => {
        request.mockResolvedValue({ eval_results: [{ failure: false }] });
        expect((await protect.protect('input', null, undefined, true, 10, true)).status).toBe('passed');
    });
});
