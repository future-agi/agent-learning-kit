import { secretsDetection } from '../metrics/code/secrets-detection.js';
import { allSecurityChecks } from '../metrics/code/index.js';

// Synthetic marker text only; none of these fixtures represents a credential.
const ordinaryLiteral = ['fixture', 'only', 'value'].join('_');
const markerLiterals = [
    { id: 'getenv', value: ['fixture', 'getenv', 'value'].join('_') },
    { id: 'process-env', value: ['fixture', 'process.env', 'value'].join('_') },
    { id: 'os-environ', value: ['fixture', 'os.environ', 'value'].join('_') },
    { id: 'env-bracket', value: ['fixture', 'ENV[fixture]', 'value'].join('_') },
    { id: 'getenv-call-text', value: 'os.getenv(FIXTURE_SETTING)' },
    { id: 'process-property-text', value: 'process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY' }
];
const quotes = [
    { id: 'double', quote: '"' },
    { id: 'single', quote: "'" }
];
const environments = [
    { id: 'process-dot', expression: 'process.env.SYNTHETIC_SETTING' },
    { id: 'process-index', expression: 'process.env["SYNTHETIC_SETTING"]' },
    { id: 'os-environ-index', expression: 'os.environ["SYNTHETIC_SETTING"]' },
    { id: 'os-environ-get', expression: 'os.environ.get("SYNTHETIC_SETTING")' },
    { id: 'os-getenv', expression: 'os.getenv("SYNTHETIC_SETTING")' },
    { id: 'env-index', expression: 'ENV["SYNTHETIC_SETTING"]' }
];
const bearerLiterals = [
    { id: 'process-property-text', value: 'process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY' },
    { id: 'environ-text', value: 'os.environ.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY' },
    { id: 'getenv-text', value: ['fixture', 'getenv', 'marker', 'for', 'fixture', 'only'].join('_') }
];
const routes = [
    { id: 'direct', run: secretsDetection },
    { id: 'combined', run: allSecurityChecks }
];

function expectDetected(
    result: ReturnType<typeof secretsDetection>,
    type: string,
    literal: string,
    line = 1
): void {
    expect(result.passed).toBe(false);
    expect(result.score).toBeLessThan(1);
    expect(result.severityCounts.high).toBeGreaterThanOrEqual(1);
    expect(result.issues.some(issue => issue.type === type && issue.severity === 'high' && issue.line === line)).toBe(true);
    expect(result.issues.every(issue => issue.match === '[REDACTED]')).toBe(true);
    // Keep assertion diagnostics value-free even if redaction regresses.
    expect(JSON.stringify(result).includes(literal)).toBe(false);
}

function expectClean(result: ReturnType<typeof secretsDetection>): void {
    expect(result.score).toBe(1);
    expect(result.passed).toBe(true);
    expect(result.issues.length).toBe(0);
}

describe.each(routes)('W04 r5 secrets literal regression: $id', ({ run }) => {
    test.each(markerLiterals.flatMap(literal => quotes.map(quote => ({
        id: `${literal.id}-${quote.id}`, value: literal.value, quote: quote.quote
    }))))('R5-S01-$id reports marker-containing password literals', ({ value, quote }) => {
        const result = run({ code: `const password = ${quote}${value}${quote};` });
        expectDetected(result, 'hardcoded-password', value);
        expect(result.issues.some(issue => issue.description === 'Hardcoded password or secret detected')).toBe(true);
    });

    test.each(bearerLiterals.flatMap(literal => quotes.map(quote => ({
        id: `${literal.id}-${quote.id}`, value: literal.value, quote: quote.quote
    }))))('R5-S02-$id reports quoted bearer values rather than treating them as lookups', ({ value, quote }) => {
        expectDetected(run({ code: `const bearer = ${quote}${value}${quote};` }), 'bearer-token', value);
    });

    test('R5-S03 a generic API-key literal containing a marker stays reportable', () => {
        const value = ['fixture', 'getenv', 'marker', 'for', 'fixture', 'only'].join('_');
        expectDetected(run({ code: `const api_key = "${value}";` }), 'api-key', value);
    });

    test('R5-S04 an ordinary synthetic literal remains detected', () => {
        expectDetected(run({ code: `const password = "${ordinaryLiteral}";` }), 'hardcoded-password', ordinaryLiteral);
    });

    test.each(environments)('R5-S05-$id genuine environment assignment remains clean', ({ expression }) => {
        expectClean(run({ code: `password = ${expression};` }));
    });

    test.each([
        { id: 'assignment', code: 'const bearer = process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY;' },
        { id: 'property', code: 'const settings = { bearer: process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY };' },
        { id: 'uppercase-key', code: 'const BEARER = process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY;' }
    ])('R5-S06-$id an unquoted bearer lookup is not a hardcoded value', ({ code }) => {
        expectClean(run({ code }));
    });

    // Migrated in r7 (owner disposition requested): a quoted property key that
    // looks like a bearer header is reported conservatively, because the match
    // cannot prove its own lexical context. Unquoted keys above stay clean.
    test.each([
        { id: 'quoted-property', code: 'const settings = { "bearer": process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY };' },
        { id: 'single-quoted-property', code: "const settings = { 'bearer': process.env.SYNTHETIC_SETTING_FOR_FIXTURE_ONLY };" }
    ])('R5-S06-$id a quoted bearer property key is reported conservatively', ({ code }) => {
        const result = run({ code });
        expect(result.passed).toBe(false);
        expect(result.issues.some(issue => issue.type === 'bearer-token' && issue.match === '[REDACTED]')).toBe(true);
        expect(JSON.stringify(result)).not.toContain('SYNTHETIC_SETTING_FOR_FIXTURE_ONLY');
    });

    test.each([
        { id: 'marker-not-expression', value: 'fixture_process.env_marker_for_fixture_only' },
        { id: 'invalid-property', value: 'process.env.FIXTURE-NOT_AN_ENV_PROPERTY_FOR_FIXTURE_ONLY' }
    ])('R5-S07-$id a marker is not sufficient to exempt an unquoted bearer match', ({ value }) => {
        expectDetected(run({ code: `const bearer = ${value};` }), 'bearer-token', value);
    });

    test.each([
        { id: 'slash', prefix: '//' },
        { id: 'hash', prefix: '#' },
        { id: 'star', prefix: '*' }
    ])('R5-S08-$id keeps the explicit comment opt-out', ({ prefix }) => {
        const value = markerLiterals[0].value;
        const code = `${prefix} password = "${value}";`;
        expectDetected(run({ code }), 'hardcoded-password', value);
        expectDetected(run({ code }, { checkComments: true }), 'hardcoded-password', value);
        expectClean(run({ code }, { checkComments: false }));
    });

    test.each(environments.flatMap(environment => ['before', 'after'].map(order => ({
        id: `${environment.id}-${order}`, expression: environment.expression, order
    }))))('R5-S09-$id an unrelated same-line lookup cannot hide a separate literal', ({ expression, order }) => {
        const lookup = `region = ${expression};`;
        const literal = `password = "${ordinaryLiteral}";`;
        const code = (order === 'before' ? [lookup, literal] : [literal, lookup]).join(' ');
        expectDetected(run({ code }), 'hardcoded-password', ordinaryLiteral);
    });

    test('R5-S10 marker-containing literal keeps its source line and full redaction', () => {
        const value = markerLiterals[2].value;
        const code = `const region = process.env.SYNTHETIC_SETTING;\nconst password = "${value}";`;
        const result = run({ code, language: 'javascript' });
        expectDetected(result, 'hardcoded-password', value, 2);
        expect(result.language).toBe('javascript');
    });

    test('R5-S11 malformed config retains the fixed diagnostic', () => {
        const value = markerLiterals[0].value;
        const result = run({ code: `const password = "${value}";` }, { threshold: NaN });
        expect(result.score).toBe(0);
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('METRIC_CONFIG_INVALID');
        expect(result.issues.some(issue => issue.type === 'validation-incomplete')).toBe(true);
        expect(JSON.stringify(result).includes(value)).toBe(false);
    });

    test('R5-S12 malformed custom pattern retains the fixed diagnostic', () => {
        const value = markerLiterals[0].value;
        const result = run({ code: `const password = "${value}";` }, { customPatterns: ['('] });
        expect(result.score).toBe(0);
        expect(result.passed).toBe(false);
        expect(result.reason).toBe('INVALID_SECURITY_PATTERN');
        expect(result.issues.some(issue => issue.type === 'validation-incomplete')).toBe(true);
        expect(JSON.stringify(result).includes(value)).toBe(false);
    });
});
