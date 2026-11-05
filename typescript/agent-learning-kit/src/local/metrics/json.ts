/**
 * JSON-based heuristic metrics for local evaluation.
 * Aligned with Python SDK's fi.evals.local.metrics.json module.
 */

import { MetricResult, MetricConfig } from './types.js';
import { snapshotBoundary } from '../autoeval/types.js';

/**
 * Check if the response contains valid JSON
 */
export function containsJson(response: string): MetricResult {
    if (typeof response !== 'string') return { score: 0, passed: false, reason: 'JSON_INVALID' };
    // Try to find JSON in the response
    const jsonPatterns = [
        /```json\s*([\s\S]*?)\s*```/,  // Markdown code block
        /```\s*([\s\S]*?)\s*```/,       // Generic code block
        /(\{[\s\S]*\})/,                // Object
        /(\[[\s\S]*\])/                 // Array
    ];

    for (const pattern of jsonPatterns) {
        const match = response.match(pattern);
        if (match) {
            try {
                JSON.parse(match[1]);
                return {
                    score: 1.0,
                    passed: true,
                    reason: 'Response contains valid JSON'
                };
            } catch {
                // Continue trying other patterns
            }
        }
    }

    // Try parsing the entire response
    try {
        JSON.parse(response);
        return {
            score: 1.0,
            passed: true,
            reason: 'Response is valid JSON'
        };
    } catch {
        return {
            score: 0.0,
            passed: false,
            reason: 'Response does not contain valid JSON'
        };
    }
}

/**
 * Check if the response is valid JSON
 */
export function isJson(response: string): MetricResult {
    if (typeof response !== 'string') return { score: 0, passed: false, reason: 'JSON_INVALID' };
    try {
        JSON.parse(response.trim());
        return {
            score: 1.0,
            passed: true,
            reason: 'Response is valid JSON'
        };
    } catch (e) {
        return {
            score: 0.0,
            passed: false,
            reason: 'JSON_INVALID'
        };
    }
}

/**
 * Validate JSON against the supported, fail-closed schema subset.
 * Supports type, required, properties, additionalProperties (boolean), items,
 * min/maxItems, min/maxLength, pattern, primitive enum, minimum and maximum.
 * Nested boolean schemas are supported; a missing, true or empty root is no
 * validation evidence. Unsupported keywords, malformed schemas, cycles and
 * depth > 64 are rejected even in properties absent from the response.
 */
export function jsonSchema(
    response: string,
    config: MetricConfig & { schema: object }
): MetricResult {
    try { config = snapshotBoundary(config, 'SCHEMA_UNSUPPORTED'); }
    catch { return { score: 0, passed: false, reason: 'SCHEMA_UNSUPPORTED' }; }
    if (!config || typeof config !== 'object' || Array.isArray(config)) return { score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' };
    if (typeof response !== 'string') return { score: 0, passed: false, reason: 'JSON_INVALID' };
    // Parse the response
    let parsed: any;
    try {
        parsed = JSON.parse(response.trim());
    } catch (e) {
        return {
            score: 0.0,
            passed: false,
            reason: 'JSON_INVALID'
        };
    }

    let schema: any;
    try { schema = snapshotSchema(config?.schema); }
    catch { return { score: 0, passed: false, reason: 'SCHEMA_UNSUPPORTED' }; }
    if (schema === undefined || schema === null || schema === true ||
        (typeof schema === 'object' && !Array.isArray(schema) && Object.keys(schema).length === 0)) {
        return { score: 0, passed: false, reason: 'SCHEMA_MISSING' };
    }
    if (!supportedSchema(schema)) return { score: 0, passed: false, reason: 'SCHEMA_UNSUPPORTED' };
    const errors = validateAgainstSchema(parsed, schema, '');

    if (errors.length === 0) {
        return {
            score: 1.0,
            passed: true,
            reason: 'Response matches JSON schema'
        };
    } else {
        return {
            score: 0.0,
            passed: false,
            reason: 'SCHEMA_VALIDATION_FAILED'
        };
    }
}

const SCHEMA_KEYS = new Set(['type', 'required', 'properties', 'additionalProperties', 'items',
    'minItems', 'maxItems', 'minLength', 'maxLength', 'pattern', 'enum', 'minimum', 'maximum']);
const JSON_TYPES = new Set(['object', 'array', 'string', 'number', 'integer', 'boolean', 'null']);

// Own a single snapshot before validation; getters/iterators cannot change the
// schema between the supported-subset check and the actual comparison.
function snapshotSchema(value: any): any {
    return snapshotBoundary(value, 'SCHEMA_UNSUPPORTED');
}

function supportedSchema(schema: any, depth = 0, ancestors = new Set<object>()): boolean {
    if (typeof schema === 'boolean') return true;
    if (!schema || typeof schema !== 'object' || Array.isArray(schema) || depth > 64 || ancestors.has(schema)) return false;
    if (Object.keys(schema).some(key => !SCHEMA_KEYS.has(key))) return false;
    if (schema.type !== undefined) {
        const types = Array.isArray(schema.type) ? schema.type : [schema.type];
        if (types.length === 0 || types.some((type: unknown) => typeof type !== 'string' || !JSON_TYPES.has(type))) return false;
    }
    if (schema.required !== undefined && (!Array.isArray(schema.required) || schema.required.some((key: unknown) => typeof key !== 'string'))) return false;
    if (schema.additionalProperties !== undefined && typeof schema.additionalProperties !== 'boolean') return false;
    for (const key of ['minItems', 'maxItems', 'minLength', 'maxLength']) {
        if (schema[key] !== undefined && (!Number.isSafeInteger(schema[key]) || schema[key] < 0)) return false;
    }
    for (const key of ['minimum', 'maximum']) {
        if (schema[key] !== undefined && (typeof schema[key] !== 'number' || !Number.isFinite(schema[key]))) return false;
    }
    if (schema.enum !== undefined && (!Array.isArray(schema.enum) || schema.enum.length === 0 ||
        schema.enum.some((value: unknown) => value !== null && typeof value !== 'string' && typeof value !== 'boolean' &&
            !(typeof value === 'number' && Number.isFinite(value))))) return false;
    if (schema.pattern !== undefined) {
        if (typeof schema.pattern !== 'string') return false;
        try { new RegExp(schema.pattern); } catch { return false; }
    }
    const next = new Set(ancestors).add(schema);
    if (schema.properties !== undefined) {
        if (!schema.properties || typeof schema.properties !== 'object' || Array.isArray(schema.properties)) return false;
        if (Object.values(schema.properties).some(child => !supportedSchema(child, depth + 1, next))) return false;
    }
    return schema.items === undefined || supportedSchema(schema.items, depth + 1, next);
}

/** Simple validator for the prevalidated subset above. */
function validateAgainstSchema(
    value: any,
    schema: any,
    path: string
): string[] {
    const errors: string[] = [];

    if (schema === false) return ['SCHEMA_FALSE'];
    if (schema === true) return errors;
    if (schema.enum && !schema.enum.includes(value)) errors.push('ENUM_MISMATCH');

    // Type validation
    if (schema.type) {
        const actualType = getJsonType(value);
        const expectedTypes = Array.isArray(schema.type) ? schema.type : [schema.type];

        // 'number' type should also accept 'integer' (JSON Schema convention)
        const typeMatches = expectedTypes.some((t: string) => {
            if (t === 'number' && actualType === 'integer') return true;
            return t === actualType;
        });

        if (!typeMatches) {
            errors.push(`${path || 'root'}: expected type ${expectedTypes.join('|')}, got ${actualType}`);
            return errors; // Return early if type doesn't match
        }
    }

    // Object validation
    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
        // Required properties
        if (schema.required && Array.isArray(schema.required)) {
            for (const prop of schema.required) {
                if (!Object.prototype.hasOwnProperty.call(value, prop)) {
                    errors.push(`${path || 'root'}: missing required property "${prop}"`);
                }
            }
        }

        // Property validation
        if (schema.properties) {
            for (const [prop, propSchema] of Object.entries(schema.properties)) {
                if (Object.prototype.hasOwnProperty.call(value, prop)) {
                    const propPath = path ? `${path}.${prop}` : prop;
                    errors.push(...validateAgainstSchema(value[prop], propSchema, propPath));
                }
            }
        }

        // Additional properties
        if (schema.additionalProperties === false) {
            const allowedProps = new Set(Object.keys(schema.properties || {}));
            for (const prop of Object.keys(value)) {
                if (!allowedProps.has(prop)) {
                    errors.push(`${path || 'root'}: unexpected property "${prop}"`);
                }
            }
        }
    }

    // Array validation
    if (Array.isArray(value)) {
        if (schema.minItems !== undefined && value.length < schema.minItems) {
            errors.push(`${path || 'root'}: array length ${value.length} is less than minItems ${schema.minItems}`);
        }
        if (schema.maxItems !== undefined && value.length > schema.maxItems) {
            errors.push(`${path || 'root'}: array length ${value.length} is greater than maxItems ${schema.maxItems}`);
        }
        if (schema.items !== undefined) {
            value.forEach((item, index) => {
                const itemPath = `${path || 'root'}[${index}]`;
                errors.push(...validateAgainstSchema(item, schema.items, itemPath));
            });
        }
    }

    // String validation
    if (typeof value === 'string') {
        if (schema.minLength !== undefined && value.length < schema.minLength) {
            errors.push(`${path || 'root'}: string length ${value.length} is less than minLength ${schema.minLength}`);
        }
        if (schema.maxLength !== undefined && value.length > schema.maxLength) {
            errors.push(`${path || 'root'}: string length ${value.length} is greater than maxLength ${schema.maxLength}`);
        }
        if (schema.pattern) {
            const regex = new RegExp(schema.pattern);
            if (!regex.test(value)) {
                errors.push(`${path || 'root'}: string does not match pattern "${schema.pattern}"`);
            }
        }
        if (schema.enum && !schema.enum.includes(value)) {
            errors.push(`${path || 'root'}: value "${value}" is not in enum [${schema.enum.join(', ')}]`);
        }
    }

    // Number validation
    if (typeof value === 'number') {
        if (schema.minimum !== undefined && value < schema.minimum) {
            errors.push(`${path || 'root'}: value ${value} is less than minimum ${schema.minimum}`);
        }
        if (schema.maximum !== undefined && value > schema.maximum) {
            errors.push(`${path || 'root'}: value ${value} is greater than maximum ${schema.maximum}`);
        }
    }

    return errors;
}

/**
 * Get JSON Schema type of a value
 */
function getJsonType(value: any): string {
    if (value === null) return 'null';
    if (Array.isArray(value)) return 'array';
    if (typeof value === 'number') {
        return Number.isInteger(value) ? 'integer' : 'number';
    }
    return typeof value;
}
