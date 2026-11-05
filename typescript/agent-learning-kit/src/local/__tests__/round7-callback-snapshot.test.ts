import { describe, expect, it } from '@jest/globals';
import { snapshotBoundary } from '../autoeval/types.js';

// SEC-W07-R7-02 / COR-R7-01: a snapshot taken with scanner callbacks allowed
// must not be reused by a later data-only capture, and no callback may run.
describe('callback-bearing snapshots and data-only capture', () => {
    it('R7-CB01 rejects a callback-bearing snapshot in a data-only capture without calling it', () => {
        let called = false;
        const rule = snapshotBoundary({
            id: 'custom/fixture',
            check: () => true,
            toJSON: () => { called = true; throw new Error('SYNTHETIC_PRIVATE_MARKER'); },
        }, 'SCANNER_RULE_INVALID', true);
        let thrown: unknown;
        try {
            snapshotBoundary({ response: rule }, 'METRIC_CONFIG_INVALID');
        } catch (error) {
            thrown = error;
        }
        expect(thrown).toBeInstanceOf(Error);
        expect((thrown as Error).message).toBe('METRIC_CONFIG_INVALID');
        expect(String(thrown)).not.toContain('SYNTHETIC_PRIVATE_MARKER');
        expect(called).toBe(false);
    });

    it('R7-CB02 keeps reusing a callback-bearing snapshot where callbacks are allowed', () => {
        const check = () => true;
        const rule = snapshotBoundary({ id: 'custom/fixture', check }, 'SCANNER_RULE_INVALID', true);
        const again = snapshotBoundary(rule, 'SCANNER_RULE_INVALID', true);
        expect(again).toBe(rule);
        expect((again as { check: unknown }).check).toBe(check);
    });

    it('R7-CB03 keeps reusing a data-only snapshot in a data-only capture', () => {
        const record = snapshotBoundary({ response: 'text', score: 1 }, 'METRIC_CONFIG_INVALID');
        expect(snapshotBoundary(record, 'METRIC_CONFIG_INVALID')).toBe(record);
    });
});
