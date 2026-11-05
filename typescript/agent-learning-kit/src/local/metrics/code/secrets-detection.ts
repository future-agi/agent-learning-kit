/**
 * Secrets/credentials detection metric.
 * Detects hardcoded API keys, passwords, and other secrets in code.
 *
 * @module local/metrics/code/secrets-detection
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
 * Patterns that indicate hardcoded secrets
 */
const SECRET_PATTERNS: Array<{
    pattern: RegExp;
    severity: 'low' | 'medium' | 'high' | 'critical';
    type: string;
    description: string;
    suggestion: string;
}> = [
    // AWS Access Key ID
    {
        pattern: /(?:AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}/g,
        severity: 'critical',
        type: 'aws-access-key',
        description: 'AWS Access Key ID detected',
        suggestion: 'Use environment variables or AWS IAM roles'
    },
    // AWS Secret Access Key
    {
        pattern: /aws[_\-]?secret[_\-]?(?:access[_\-]?)?key["'\s:=]+[A-Za-z0-9/+=]{40}/gi,
        severity: 'critical',
        type: 'aws-secret-key',
        description: 'AWS Secret Access Key detected',
        suggestion: 'Use environment variables or AWS Secrets Manager'
    },
    // Generic API Key patterns
    {
        pattern: /(?:api[_\-]?key|apikey)["'\s:=]+["']?[A-Za-z0-9_\-]{20,}/gi,
        severity: 'high',
        type: 'api-key',
        description: 'API key detected',
        suggestion: 'Store API keys in environment variables'
    },
    // OpenAI API Key
    {
        pattern: /sk-[A-Za-z0-9]{48}/g,
        severity: 'critical',
        type: 'openai-api-key',
        description: 'OpenAI API key detected',
        suggestion: 'Use OPENAI_API_KEY environment variable'
    },
    // Anthropic API Key
    {
        pattern: /sk-ant-[A-Za-z0-9\-_]{40,}/g,
        severity: 'critical',
        type: 'anthropic-api-key',
        description: 'Anthropic API key detected',
        suggestion: 'Use ANTHROPIC_API_KEY environment variable'
    },
    // GitHub Token
    {
        pattern: /gh[pousr]_[A-Za-z0-9]{36,}/g,
        severity: 'critical',
        type: 'github-token',
        description: 'GitHub token detected',
        suggestion: 'Use GITHUB_TOKEN environment variable'
    },
    // Slack Token
    {
        pattern: /xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24}/g,
        severity: 'critical',
        type: 'slack-token',
        description: 'Slack token detected',
        suggestion: 'Store Slack tokens in environment variables'
    },
    // Stripe API Key
    {
        pattern: /sk_(?:live|test)_[A-Za-z0-9]{24,}/g,
        severity: 'critical',
        type: 'stripe-api-key',
        description: 'Stripe API key detected',
        suggestion: 'Use environment variables for Stripe keys'
    },
    // Google API Key
    {
        pattern: /AIza[A-Za-z0-9_\-]{35}/g,
        severity: 'high',
        type: 'google-api-key',
        description: 'Google API key detected',
        suggestion: 'Use environment variables for Google API keys'
    },
    // Firebase API Key
    {
        pattern: /firebase[_\-]?(?:api[_\-]?)?key["'\s:=]+["']?[A-Za-z0-9_\-]{30,}/gi,
        severity: 'high',
        type: 'firebase-api-key',
        description: 'Firebase API key detected',
        suggestion: 'Configure Firebase keys properly for client/server use'
    },
    // Private Key
    {
        pattern: /-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/g,
        severity: 'critical',
        type: 'private-key',
        description: 'Private key detected',
        suggestion: 'Never commit private keys; use secrets management'
    },
    // Password in variable name
    {
        pattern: /(?:password|passwd|pwd|secret|credentials?)["'\s:=]+["'][^"']{6,}/gi,
        severity: 'high',
        type: 'hardcoded-password',
        description: 'Hardcoded password or secret detected',
        suggestion: 'Use environment variables or secrets manager'
    },
    // Database connection string with password
    {
        pattern: /(?:mongodb|mysql|postgres|postgresql|mssql|redis):\/\/[^:]+:[^@]+@/gi,
        severity: 'critical',
        type: 'database-connection-string',
        description: 'Database connection string with credentials',
        suggestion: 'Use environment variables for database URLs'
    },
    // Bearer token
    {
        pattern: /bearer["'\s:=]+["']?[A-Za-z0-9_\-\.]{30,}/gi,
        severity: 'high',
        type: 'bearer-token',
        description: 'Bearer token detected',
        suggestion: 'Store tokens in environment variables'
    },
    // JWT Token (complete)
    {
        pattern: /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_\-]{10,}/g,
        severity: 'medium',
        type: 'jwt-token',
        description: 'JWT token detected',
        suggestion: 'Avoid committing JWT tokens; generate at runtime'
    },
    // Twilio Auth Token
    {
        pattern: /twilio[_\-]?(?:auth[_\-]?)?token["'\s:=]+["']?[A-Za-z0-9]{32}/gi,
        severity: 'critical',
        type: 'twilio-token',
        description: 'Twilio auth token detected',
        suggestion: 'Use environment variables for Twilio credentials'
    },
    // SendGrid API Key
    {
        pattern: /SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}/g,
        severity: 'critical',
        type: 'sendgrid-api-key',
        description: 'SendGrid API key detected',
        suggestion: 'Use environment variables for SendGrid API key'
    },
    // Heroku API Key
    {
        pattern: /heroku[_\-]?api[_\-]?key["'\s:=]+["']?[A-Za-z0-9\-]{36}/gi,
        severity: 'critical',
        type: 'heroku-api-key',
        description: 'Heroku API key detected',
        suggestion: 'Use environment variables for Heroku credentials'
    },
    // NPM Token
    {
        pattern: /npm_[A-Za-z0-9]{36}/g,
        severity: 'critical',
        type: 'npm-token',
        description: 'NPM token detected',
        suggestion: 'Use NPM_TOKEN environment variable'
    }
];

/**
 * Detect hardcoded secrets in code.
 *
 * @param input - Code to analyze
 * @param config - Configuration options
 * @returns Security analysis result
 *
 * @example
 * ```typescript
 * const result = secretsDetection({
 *     code: 'const apiKey = "sk-1234567890abcdef";'
 * });
 * console.log(result.passed); // false
 * console.log(result.issues[0].type); // 'api-key'
 * ```
 */
export function secretsDetection(
    input: CodeInput,
    config: CodeSecurityConfig = {}
): CodeSecurityResult {
    try { ({ input, config } = snapshotBoundary({ input, config }, 'METRIC_CONFIG_INVALID')); }
    catch (error) { return incompleteSecurity(error instanceof Error && error.message === 'INVALID_SECURITY_PATTERN' ? 'INVALID_SECURITY_PATTERN' : 'METRIC_CONFIG_INVALID'); }
    // The shared snapshot owns records but also preserves primitives/iterables.
    // Only its nonarray object result is a configuration record.
    if (!input || !config || typeof config !== 'object' || Array.isArray(config)) return incompleteSecurity('METRIC_CONFIG_INVALID');
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
    for (const { pattern, severity, type, description, suggestion } of SECRET_PATTERNS) {
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
                line.trim().startsWith('*');

            // Literal contents are never evidence of an environment lookup.
            // Only the bearer pattern can consume an unquoted process.env
            // property value. Require that whole assignment span, not a marker
            // substring or a quoted value; other environment APIs do not match.
            const isEnvRef = type === 'bearer-token' &&
                /^[A-Za-z]+["']?\s*[:=]\s*process\.env\.[A-Za-z_$][A-Za-z0-9_$]*$/.test(match[0]);

            if (!isComment && !isEnvRef) {
                // Mask the actual secret in the match
                const maskedMatch = '[REDACTED]';

                issues.push({
                    type,
                    severity,
                    line: lineNumber,
                    match: maskedMatch,
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
                        type: 'custom-secret',
                        severity: 'high',
                        match: '[REDACTED]',
                        description: 'Custom secret pattern matched'
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
 * Alias for secretsDetection
 */
export const noSecrets = secretsDetection;
export const noHardcodedSecrets = secretsDetection;
