import { createSecurityResult } from '../metrics/code/types.js';

const invalidConfigs: Array<[string, unknown]> = [
    ['empty array', []],
    ['populated array', ['not-a-config']],
    ['array with configuration fields', Object.assign([], { threshold: 1, minSeverity: 'low' })],
];

const validConfigs: Array<[string, unknown]> = [
    ['omitted configuration', undefined],
    ['empty record', {}],
    ['explicit supported fields', { threshold: 1, minSeverity: 'low' }],
];

describe('security result configuration boundary', () => {
    it.each(invalidConfigs)('rejects %s rather than applying successful defaults', (_name, config) => {
        const result = createSecurityResult([], config as Parameters<typeof createSecurityResult>[1]);
        expect(result.passed).toBe(false);
        expect(result.score).toBe(0);
        expect(result.reason).toBe('METRIC_CONFIG_INVALID');
        expect(result.issues[0]?.type).toBe('validation-incomplete');
        expect(result.severityCounts.critical).toBe(1);
    });

    it.each(validConfigs)('preserves a completed no-issue result with %s', (_name, config) => {
        const result = createSecurityResult([], config as Parameters<typeof createSecurityResult>[1]);
        expect(result.passed).toBe(true);
        expect(result.score).toBe(1);
        expect(result.issues).toEqual([]);
    });
});
