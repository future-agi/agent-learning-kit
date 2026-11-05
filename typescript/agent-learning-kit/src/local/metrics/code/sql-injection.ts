/**
 * SQL Injection detection metric.
 * Detects potential SQL injection vulnerabilities in code.
 *
 * @module local/metrics/code/sql-injection
 */

import { snapshotBoundary } from '../../autoeval/types.js';
import { incompleteSecurity } from './types.js';
import {
    CodeInput,
    CodeSecurityConfig,
    CodeSecurityResult,
    SecurityIssue,
    createSecurityResult
} from './types.js';

/**
 * Patterns that indicate SQL injection vulnerabilities
 */
const SQL_INJECTION_PATTERNS: Array<{
    pattern: RegExp;
    severity: 'low' | 'medium' | 'high' | 'critical';
    description: string;
    suggestion: string;
}> = [
    // String concatenation in SQL queries
    {
        pattern: /(?:SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE)[\s\S]*?\+[\s]*(?:req\.|request\.|params\.|query\.|body\.|user|input|\$|`)/gi,
        severity: 'critical',
        description: 'Direct string concatenation with user input in SQL query',
        suggestion: 'Use parameterized queries or prepared statements'
    },
    // Template literals with user input
    {
        pattern: /(?:SELECT|INSERT|UPDATE|DELETE|DROP)[\s\S]*?\$\{(?:req\.|request\.|params\.|query\.|body\.|user|input)/gi,
        severity: 'critical',
        description: 'Template literal interpolation with user input in SQL query',
        suggestion: 'Use parameterized queries instead of template literals'
    },
    // f-strings in Python with SQL
    {
        pattern: /f["'](?:SELECT|INSERT|UPDATE|DELETE|DROP)[\s\S]*?\{/gi,
        severity: 'critical',
        description: 'Python f-string interpolation in SQL query',
        suggestion: 'Use parameterized queries with cursor.execute(sql, params)'
    },
    // .format() in Python with SQL
    {
        pattern: /["'](?:SELECT|INSERT|UPDATE|DELETE|DROP)[\s\S]*?["']\.format\s*\(/gi,
        severity: 'critical',
        description: 'Python .format() in SQL query',
        suggestion: 'Use parameterized queries instead of string formatting'
    },
    // % formatting in Python
    {
        pattern: /["'](?:SELECT|INSERT|UPDATE|DELETE|DROP)[\s\S]*?%s[\s\S]*?["']\s*%/gi,
        severity: 'high',
        description: 'Python % string formatting in SQL query',
        suggestion: 'Use parameterized queries with placeholders'
    },
    // Raw SQL execution without parameterization
    {
        pattern: /\.(?:execute|query|raw)\s*\(\s*["'`](?:SELECT|INSERT|UPDATE|DELETE|DROP)/gi,
        severity: 'medium',
        description: 'Raw SQL execution detected - verify parameterization',
        suggestion: 'Ensure query uses parameterized values'
    },
    // LIKE with direct concatenation
    {
        pattern: /LIKE\s*['"]?\s*%?\s*['"]?\s*\+/gi,
        severity: 'high',
        description: 'LIKE clause with string concatenation',
        suggestion: 'Use parameterized LIKE with proper escaping'
    },
    // ORDER BY with user input
    {
        pattern: /ORDER\s+BY\s*["'`]?\s*\+|ORDER\s+BY\s*\$\{/gi,
        severity: 'high',
        description: 'ORDER BY clause with dynamic input',
        suggestion: 'Whitelist allowed column names instead of using user input'
    },
    // IN clause with array join
    {
        pattern: /IN\s*\(\s*['"]?\s*\+[\s\S]*?\.join/gi,
        severity: 'high',
        description: 'IN clause constructed from array join',
        suggestion: 'Use parameterized IN clause with proper escaping'
    }
];

/**
 * Detect SQL injection vulnerabilities in code.
 *
 * @param input - Code to analyze
 * @param config - Configuration options
 * @returns Security analysis result
 *
 * @example
 * ```typescript
 * const result = sqlInjection({
 *     code: 'db.query("SELECT * FROM users WHERE id = " + userId)'
 * });
 * console.log(result.passed); // false
 * console.log(result.issues[0].severity); // 'critical'
 * ```
 */
export function sqlInjection(
    input: CodeInput,
    config: CodeSecurityConfig = {}
): CodeSecurityResult {
    try { ({ input, config } = snapshotBoundary({ input, config }, 'METRIC_CONFIG_INVALID')); }
    catch (error) { return incompleteSecurity(error instanceof Error && error.message === 'INVALID_SECURITY_PATTERN' ? 'INVALID_SECURITY_PATTERN' : 'METRIC_CONFIG_INVALID'); }
    if (!input || typeof input !== 'object' || Array.isArray(input) ||
        !config || typeof config !== 'object' || Array.isArray(config)) return incompleteSecurity('METRIC_CONFIG_INVALID');
    const { code, language } = input;
    const issues: SecurityIssue[] = [];
    const threshold = config.threshold === undefined ? 1 : config.threshold;
    const severity = config.minSeverity === undefined ? 'low' : config.minSeverity;
    if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1 ||
        typeof severity !== 'string' || !['low', 'medium', 'high', 'critical'].some(value => severity === value) ||
        (config.checkComments !== undefined && typeof config.checkComments !== 'boolean')) {
        return { ...createSecurityResult([{ type: 'validation-incomplete', severity: 'critical', match: '', description: 'METRIC_CONFIG_INVALID' }]), score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    }

    if (typeof code !== 'string' || code.trim().length === 0) {
        return { ...createSecurityResult([{ type: 'validation-incomplete', severity: 'critical', match: '', description: 'NO_EVIDENCE' }], config), score: 0, passed: false, reason: 'NO_EVIDENCE' };
    }

    const lines = code.split('\n');

    // Check each pattern
    for (const { pattern, severity, description, suggestion } of SQL_INJECTION_PATTERNS) {
        // Reset regex state
        pattern.lastIndex = 0;

        let match;
        while ((match = pattern.exec(code)) !== null) {
            // Find line number
            const matchIndex = match.index;
            let lineNumber = 1;
            let charCount = 0;
            for (let i = 0; i < lines.length; i++) {
                charCount += lines[i].length + 1; // +1 for newline
                if (charCount > matchIndex) {
                    lineNumber = i + 1;
                    break;
                }
            }

            // Check if this is in a comment (basic check)
            const line = lines[lineNumber - 1] || '';
            const isComment = config.checkComments !== false ? false :
                line.trim().startsWith('//') ||
                line.trim().startsWith('#') ||
                line.trim().startsWith('*') ||
                line.trim().startsWith('--');

            if (!isComment) {
                issues.push({
                    type: 'sql-injection',
                    severity,
                    line: lineNumber,
                    match: '[REDACTED]', // Truncate long matches
                    description,
                    suggestion
                });
            }
        }
    }

    // Snapshot caller iterables once; never validate a consumed iterator.
    let patterns: string[];
    try {
        const value = config.customPatterns;
        if (value !== undefined && (value === null || typeof value !== 'object' || typeof value[Symbol.iterator] !== 'function')) throw new Error();
        patterns = value === undefined ? [] : Array.from(value);
        if (patterns.some(pattern => typeof pattern !== 'string')) throw new Error();
    } catch {
        return { ...createSecurityResult([...issues, { type: 'validation-incomplete', severity: 'critical', match: '', description: 'INVALID_SECURITY_PATTERN' }], config), score: 0, passed: false, reason: 'INVALID_SECURITY_PATTERN' };
    }
    {
        for (const patternStr of patterns) {
            try {
                if (typeof patternStr !== 'string') throw new Error('INVALID_SECURITY_PATTERN');
                const customPattern = new RegExp(patternStr, 'gi');
                let match;
                while ((match = customPattern.exec(code)) !== null) {
                    if (match[0].length === 0) throw new Error('INVALID_SECURITY_PATTERN');
                    const line = code.slice(code.lastIndexOf('\n', match.index - 1) + 1, match.index + match[0].length).trim();
                    if (config.checkComments === false && /^(?:\/\/|#|\*|--|\{\/\*)/.test(line)) continue;
                    issues.push({
                        type: 'sql-injection-custom',
                        severity: 'medium',
                        match: '[REDACTED]',
                        description: 'Custom SQL injection pattern matched'
                    });
                }
            } catch {
                return { ...createSecurityResult([...issues, { type: 'validation-incomplete', severity: 'critical', match: '', description: 'INVALID_SECURITY_PATTERN' }], config), score: 0, passed: false, reason: 'INVALID_SECURITY_PATTERN' };
            }
        }
    }

    const result = createSecurityResult(issues, config);
    result.language = language;
    return result;
}

/**
 * Alias for sqlInjection
 */
export const noSqlInjection = sqlInjection;
