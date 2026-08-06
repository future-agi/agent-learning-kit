import { Protect } from '../protect';
import { Evaluator } from '../evaluator';
import { InvalidAuthError, SDKException } from '../core';

describe('Protect', () => {
    let protect: Protect;
    let evaluator: Evaluator;
    let requestSpy: jest.SpyInstance;

    beforeEach(() => {
        // Instantiate the real Evaluator, providing dummy credentials
        evaluator = new Evaluator({ fiApiKey: 'test-key', fiSecretKey: 'test-secret' });
        // Spy on the 'request' method of the real evaluator instance
        requestSpy = jest.spyOn(evaluator, 'request');
        // Instantiate Protect with the real evaluator
        protect = new Protect({ evaluator });
    });

    afterEach(() => {
        // Restore the original method to avoid mock bleed-over between tests
        requestSpy.mockRestore();
    });

    describe('Constructor', () => {
        it('should use the provided evaluator instance', () => {
            expect(protect.evaluator).toBe(evaluator);
        });

        it('should throw InvalidAuthError if no credentials are found', () => {
            // Unset env vars to simulate missing credentials
            const oldApiKey = process.env.FI_API_KEY;
            const oldSecretKey = process.env.FI_SECRET_KEY;
            delete process.env.FI_API_KEY;
            delete process.env.FI_SECRET_KEY;
            
            // Expect the constructor to throw when no keys are available
            expect(() => new Protect()).toThrow(InvalidAuthError);
            
            // Restore env vars
            process.env.FI_API_KEY = oldApiKey;
            process.env.FI_SECRET_KEY = oldSecretKey;
        });
    });

    describe('Input and Rule Validation', () => {
        it('should throw an error for invalid inputs', async () => {
            // TS side currently accepts a single string only (Python parity gap - Python accepts string | list).
            await expect(protect.protect(null as any)).rejects.toThrow('inputs with value null is of type object, but expected from string');
        });

        it('should throw an error for invalid protectRules', async () => {
            await expect(protect.protect('test', [{ metric: 'InvalidMetric' }])).rejects.toThrow('metric in Rule at index 0 with value "InvalidMetric" is of type string, but expected from one of');
        });
    });

    describe('Protect Modes', () => {
        const mockInput = 'This is a test input.';

        describe('Flash Mode', () => {
            it('should return "passed" when flash mode detects no harm', async () => {
                requestSpy.mockResolvedValue({
                    eval_results: [{ failure: false, runtime: 50 }]
                });
                const result = await protect.protect(mockInput, null, undefined, undefined, undefined, true);
                expect(result.status).toBe('passed');
            });

            it('should return "failed" when flash mode detects harm', async () => {
                requestSpy.mockResolvedValue({
                    eval_results: [{ failure: true, runtime: 50 }]
                });
                const result = await protect.protect(mockInput, null, undefined, undefined, undefined, true);
                expect(result.status).toBe('failed');
            });
        });

        describe('Standard Mode', () => {
            it('should return "passed" when no rules are triggered', async () => {
                requestSpy.mockResolvedValue({
                    eval_results: [{ data: ['Not Toxic'], failure: false, reason: '' }]
                });
                const result = await protect.protect(mockInput, [{ metric: 'toxicity' }]);
                expect(result.status).toBe('passed');
                expect(result.completed_rules).toContain('toxicity');
            });

            it('should return "failed" when a rule is triggered', async () => {
                requestSpy.mockResolvedValue({
                    eval_results: [{ data: ['Failed'], failure: true, reason: 'High toxicity score' }]
                });
                const result = await protect.protect(mockInput, [{ metric: 'toxicity' }], 'Blocked', true);
                expect(result.status).toBe('failed');
                expect(result.reasons).toBe('High toxicity score');
            });
        });
    });

    describe('Canonical + legacy metric parity with Python', () => {
        const canonical = [
            'toxicity',
            'bias_detection',
            'prompt_injection',
            'data_privacy_compliance',
        ];
        const legacyAliases = [
            'Toxicity',
            'Sexism',
            'Prompt Injection',
            'Data Privacy',
            'content_moderation',
            'security',
        ];

        beforeEach(() => {
            requestSpy.mockResolvedValue({
                eval_results: [{ data: ['Not Toxic'], failure: false, reason: '' }],
            });
        });

        it.each(canonical)('accepts canonical metric %s', async (metric) => {
            const result = await protect.protect('hi', [{ metric }]);
            expect(result.status).toBe('passed');
            expect(result.completed_rules).toContain(metric);
        });

        it.each(legacyAliases)('accepts legacy alias %s with a deprecation warning', async (metric) => {
            const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
            const result = await protect.protect('hi', [{ metric }]);
            expect(result.status).toBe('passed');
            expect(warnSpy).toHaveBeenCalledWith(
                expect.stringContaining(`Protect metric "${metric}" is deprecated`),
            );
            warnSpy.mockRestore();
        });

        it('rejects the previously-supported "Tone" metric', async () => {
            await expect(
                protect.protect('hi', [{ metric: 'Tone', contains: ['friendly'] }]),
            ).rejects.toThrow(/metric in Rule at index 0/);
        });
    });
});