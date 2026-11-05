import { describe, expect, it } from '@jest/globals';
import { secretsDetection } from '../metrics/code/secrets-detection.js';
import { allSecurityChecks } from '../metrics/code/index.js';

// Synthetic source text only; no environment reads or usable credentials.
const setting = 'SYNTHETIC_SETTING_FOR_FIXTURE_ONLY';
const envRef = `process.env.${setting}`;
const header = `Bearer: ${envRef}`;

const literalCases = [
    { name: 'double-quoted header', code: `const header = "${header}";` },
    { name: 'single-quoted header', code: `const header = '${header}';` },
    { name: 'template header', code: 'const header = `' + header + '`;' },
    // The delimiter is inside the literal, so a suffix-only check is insufficient.
    { name: 'quoted header with an internal delimiter', code: `const header = "${header}; fixture";` },
    { name: 'single-quoted header with an internal delimiter', code: `const header = '${header}; fixture';` },
    { name: 'escaped double quote before the match', code: String.raw`const header = "prefix \"${header}; fixture";` },
    { name: 'escaped single quote before the match', code: String.raw`const header = 'prefix \'${header}; fixture';` },
    { name: 'template with an internal delimiter', code: 'const header = `' + header + '; fixture`;' },
    { name: 'template after a quoted interpolation', code: 'const header = `${"fixture"} ' + header + '; fixture`;' },
    { name: 'template after a nested template', code: 'const header = `${`nested`} ' + header + '; fixture`;' },
    { name: 'assignment inside template interpolation', code: 'const header = `${bearer = ' + envRef + '}`;' },
    { name: 'multiline template', code: 'const header = `first\n' + header + ';\nlast`;' },
    { name: 'triple-quoted string', code: 'header = """first\n' + header + ';\nlast"""' },
    { name: 'prefixed string', code: `header = f"${header}; fixture"` },
    { name: 'regex literal', code: `const pattern = /${header}; fixture/;` },
    { name: 'unclosed string before the match', code: `const header = "first\n${header};` },
];

const cleanCases = [
    { name: 'unquoted declaration', code: `const bearer = ${envRef};` },
    { name: 'unquoted assignment at end of source', code: `bearer = ${envRef}` },
    { name: 'unquoted property', code: `const headers = { bearer: ${envRef} };` },
    { name: 'mixed-case property', code: `const headers = { Bearer: ${envRef} };` },
    { name: 'member assignment', code: `headers.bearer = ${envRef};` },
    { name: 'environment identifier containing a dollar sign', code: `const bearer = ${envRef}$suffix;` },
    { name: 'multiple unquoted assignments', code: `let bearer = ${envRef};\nbearer = ${envRef};` },
    { name: 'assignment after a normal string', code: `const note = "fixture";\nconst bearer = ${envRef};` },
    { name: 'assignment after a string containing a backtick', code: 'const note = "`";\nconst bearer = ' + envRef + ';' },
    { name: 'assignment after escaped quotes', code: String.raw`const note = "fixture \"quoted\""; const bearer = ${envRef};` },
    { name: 'assignment after comment quote markers', code: '// " \' `\nconst bearer = ' + envRef + ';' },
    { name: 'assignment after block comment quote markers', code: '/* " \' ` */ const bearer = ' + envRef + ';' },
    { name: 'Python environment subscript', code: `bearer = os.environ["${setting}"]` },
    { name: 'Python environment lookup', code: `bearer = os.environ.get("${setting}")` },
];

for (const [route, scan] of [
    ['secretsDetection', secretsDetection],
    ['allSecurityChecks', allSecurityChecks],
] as const) {
    describe(`${route}: conservative bearer context`, () => {
        for (const { name, code } of literalCases) {
            it(`reports ${name} without leaking its contents`, () => {
                const result = scan({ code });
                const bearerIssues = result.issues.filter(issue => issue.type === 'bearer-token');
                expect(result.passed).toBe(false);
                expect(result.score).toBeLessThan(1);
                expect(bearerIssues.length).toBeGreaterThan(0);
                for (const issue of bearerIssues) {
                    expect(issue.match).toBe('[REDACTED]');
                    expect(issue.description).toBe('Bearer token detected');
                    expect(issue.severity).toBe('high');
                }
                const serialized = JSON.stringify(result);
                expect(serialized).not.toContain(setting);
                expect(serialized).not.toContain(envRef);
            });
        }

        for (const { name, code } of cleanCases) {
            it(`keeps ${name} clean`, () => {
                const result = scan({ code });
                expect(result.passed).toBe(true);
                expect(result.score).toBe(1);
                expect(result.issues).toEqual([]);
            });
        }

        it('keeps each occurrence separate when a real assignment precedes a literal', () => {
            const code = `const bearer = ${envRef};\nconst header = "${header}; fixture";`;
            const result = scan({ code });
            expect(result.passed).toBe(false);
            const issues = result.issues.filter(issue => issue.type === 'bearer-token');
            expect(issues).toHaveLength(1);
            expect(issues[0].line).toBe(2);
            expect(issues[0].match).toBe('[REDACTED]');
        });

        it('does not let a preceding quoted match suppress a later real assignment', () => {
            const code = `const header = "${header}; fixture";\nconst bearer = ${envRef};`;
            const result = scan({ code });
            expect(result.passed).toBe(false);
            const issues = result.issues.filter(issue => issue.type === 'bearer-token');
            expect(issues).toHaveLength(1);
            expect(issues[0].line).toBe(1);
        });

        it('preserves line numbers in multiline literals', () => {
            const code = 'const header = `first\n' + header + ';\nlast`;';
            const result = scan({ code });
            expect(result.issues).toEqual(expect.arrayContaining([
                expect.objectContaining({ type: 'bearer-token', line: 2, match: '[REDACTED]' }),
            ]));
        });

        it('refuses to resume exemptions after ambiguous template syntax', () => {
            // Conservative false positive by design: no template parser is claimed.
            const code = 'const note = `fixture`;\nconst bearer = ' + envRef + ';';
            const result = scan({ code });
            expect(result.passed).toBe(false);
            expect(result.issues).toEqual(expect.arrayContaining([
                expect.objectContaining({ type: 'bearer-token', line: 2, match: '[REDACTED]' }),
            ]));
        });

        it('does not exempt an API lookalike or a compound RHS', () => {
            for (const code of [
                `const bearer = PROCESS.ENV.${setting};`,
                `const bearer = ${envRef} + "fixture";`,
            ]) {
                const result = scan({ code });
                expect(result.passed).toBe(false);
                expect(result.issues).toEqual(expect.arrayContaining([
                    expect.objectContaining({ type: 'bearer-token', match: '[REDACTED]' }),
                ]));
            }
        });

        for (const comment of ['//', '#']) {
            it(`retains explicit ${comment} comment opt-out`, () => {
                const code = `${comment} ${header}; fixture`;
                expect(scan({ code }).passed).toBe(false);
                const result = scan({ code }, { checkComments: false });
                expect(result.passed).toBe(true);
                expect(result.score).toBe(1);
                expect(result.issues).toEqual([]);
            });
        }
    });
}

describe('secretsDetection: fixed diagnostics remain unchanged', () => {
    it('keeps invalid configuration diagnostics fixed', () => {
        const result = secretsDetection({ code: `const header = "${header}";` }, { threshold: NaN });
        expect(result.passed).toBe(false);
        expect(result.score).toBe(0);
        expect(result.reason).toBe('METRIC_CONFIG_INVALID');
        expect(JSON.stringify(result)).not.toContain(setting);
    });

    it('keeps invalid custom-pattern diagnostics fixed with only the incomplete-validation sentinel', () => {
        const result = secretsDetection({ code: `const header = "${header}";` }, { customPatterns: ['['] });
        expect(result.passed).toBe(false);
        expect(result.score).toBe(0);
        expect(result.reason).toBe('INVALID_SECURITY_PATTERN');
        // Incomplete validation keeps only the fixed sentinel, never earlier issues.
        expect(result.issues.map(issue => issue.type)).toEqual(['validation-incomplete']);
        expect(JSON.stringify(result)).not.toContain(setting);
    });

    it('keeps empty-input diagnostics fixed', () => {
        const result = secretsDetection({ code: '' });
        expect(result.passed).toBe(false);
        expect(result.score).toBe(0);
        expect(result.reason).toBe('NO_EVIDENCE');
    });
});
