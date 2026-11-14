/**
 * XSS (Cross-Site Scripting) detection metric.
 * Detects potential XSS vulnerabilities in code.
 *
 * @module local/metrics/code/xss-detection
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
 * Patterns that indicate XSS vulnerabilities
 */
const XSS_PATTERNS: Array<{
    pattern: RegExp;
    severity: 'low' | 'medium' | 'high' | 'critical';
    description: string;
    suggestion: string;
}> = [
    // innerHTML with user input
    {
        pattern: /\.innerHTML\s*=\s*(?:req\.|request\.|params\.|query\.|body\.|user|input|\$\{|`)/gi,
        severity: 'critical',
        description: 'innerHTML assignment with potentially untrusted input',
        suggestion: 'Use textContent or sanitize HTML before insertion'
    },
    // innerHTML with concatenation
    {
        pattern: /\.innerHTML\s*=\s*["'`]?[^"'`]*\+/gi,
        severity: 'high',
        description: 'innerHTML with string concatenation',
        suggestion: 'Use textContent or a sanitization library like DOMPurify'
    },
    // outerHTML with user input
    {
        pattern: /\.outerHTML\s*=\s*(?:req\.|request\.|params\.|query\.|body\.|user|input|\$\{)/gi,
        severity: 'critical',
        description: 'outerHTML assignment with potentially untrusted input',
        suggestion: 'Use textContent or sanitize HTML before insertion'
    },
    // document.write with user input
    {
        pattern: /document\.write\s*\(\s*(?:req\.|request\.|params\.|query\.|body\.|user|input|\$\{|`)/gi,
        severity: 'critical',
        description: 'document.write with potentially untrusted input',
        suggestion: 'Avoid document.write; use DOM manipulation methods'
    },
    // document.write with concatenation
    {
        pattern: /document\.write\s*\([^)]*\+/gi,
        severity: 'high',
        description: 'document.write with string concatenation',
        suggestion: 'Avoid document.write; use DOM manipulation methods'
    },
    // eval with user input
    {
        pattern: /eval\s*\(\s*(?:req\.|request\.|params\.|query\.|body\.|user|input|\$\{)/gi,
        severity: 'critical',
        description: 'eval() with potentially untrusted input',
        suggestion: 'Never use eval with user input; use JSON.parse for data'
    },
    // setTimeout/setInterval with string
    {
        pattern: /set(?:Timeout|Interval)\s*\(\s*["'`][^"'`]*(?:req\.|request\.|params\.|query\.|body\.|user|input)/gi,
        severity: 'high',
        description: 'setTimeout/setInterval with string containing user input',
        suggestion: 'Pass a function reference instead of a string'
    },
    // dangerouslySetInnerHTML in React
    {
        pattern: /dangerouslySetInnerHTML\s*=\s*\{\s*\{\s*__html\s*:\s*(?:req\.|request\.|params\.|query\.|body\.|user|input|props\.|state\.)/gi,
        severity: 'critical',
        description: 'React dangerouslySetInnerHTML with potentially untrusted input',
        suggestion: 'Sanitize HTML with DOMPurify before using dangerouslySetInnerHTML'
    },
    // dangerouslySetInnerHTML without sanitization mention
    {
        pattern: /dangerouslySetInnerHTML\s*=\s*\{/gi,
        severity: 'medium',
        description: 'React dangerouslySetInnerHTML usage detected',
        suggestion: 'Ensure HTML is sanitized before using dangerouslySetInnerHTML'
    },
    // v-html in Vue
    {
        pattern: /v-html\s*=\s*["'](?:user|input|query|params|request)/gi,
        severity: 'critical',
        description: 'Vue v-html with potentially untrusted input',
        suggestion: 'Sanitize HTML before using v-html directive'
    },
    // [innerHTML] in Angular
    {
        pattern: /\[innerHTML\]\s*=\s*["'](?:user|input|query|params|request)/gi,
        severity: 'critical',
        description: 'Angular innerHTML binding with potentially untrusted input',
        suggestion: 'Use Angular DomSanitizer or sanitize HTML manually'
    },
    // jQuery html() with user input
    {
        pattern: /\$\([^)]+\)\.html\s*\(\s*(?:req\.|request\.|params\.|query\.|body\.|user|input)/gi,
        severity: 'critical',
        description: 'jQuery .html() with potentially untrusted input',
        suggestion: 'Use .text() or sanitize HTML before using .html()'
    },
    // jQuery append/prepend with user input
    {
        pattern: /\$\([^)]+\)\.(?:append|prepend)\s*\(\s*["'`]?<[^>]*(?:req\.|request\.|params\.|query\.|body\.|user|input)/gi,
        severity: 'high',
        description: 'jQuery append/prepend with HTML containing user input',
        suggestion: 'Create elements programmatically or sanitize HTML'
    },
    // location.href assignment
    {
        pattern: /(?:window\.)?location(?:\.href)?\s*=\s*(?:req\.|request\.|params\.|query\.|body\.|user|input)/gi,
        severity: 'high',
        description: 'Location redirect with potentially untrusted input',
        suggestion: 'Validate and whitelist redirect URLs'
    },
    // URL parameter in script src
    {
        pattern: /<script[^>]*src\s*=\s*["']?\s*(?:\+|`|\$\{)/gi,
        severity: 'critical',
        description: 'Dynamic script src attribute',
        suggestion: 'Never use dynamic URLs for script sources'
    }
];

/**
 * Detect XSS vulnerabilities in code.
 *
 * @param input - Code to analyze
 * @param config - Configuration options
 * @returns Security analysis result
 *
 * @example
 * ```typescript
 * const result = xssDetection({
 *     code: 'element.innerHTML = userInput;'
 * });
 * console.log(result.passed); // false
 * console.log(result.issues[0].type); // 'xss'
 * ```
 */
export function xssDetection(
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
    for (const { pattern, severity, description, suggestion } of XSS_PATTERNS) {
        // Reset regex state
        pattern.lastIndex = 0;

        let match;
        while ((match = pattern.exec(code)) !== null) {
            // Find line number
            const matchIndex = match.index;
            let lineNumber = 1;
            let charCount = 0;
            for (let i = 0; i < lines.length; i++) {
                charCount += lines[i].length + 1;
                if (charCount > matchIndex) {
                    lineNumber = i + 1;
                    break;
                }
            }

            // Check if in comment
            const line = lines[lineNumber - 1] || '';
            const isComment = config.checkComments !== false ? false :
                line.trim().startsWith('//') ||
                line.trim().startsWith('#') ||
                line.trim().startsWith('*') ||
                line.trim().startsWith('{/*');

            if (!isComment) {
                issues.push({
                    type: 'xss',
                    severity,
                    line: lineNumber,
                    match: '[REDACTED]',
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
                        type: 'xss-custom',
                        severity: 'medium',
                        match: '[REDACTED]',
                        description: 'Custom XSS pattern matched'
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
 * Alias for xssDetection
 */
export const noXss = xssDetection;
