/**
 * COR-R4-01: constructor-only regressions for base numeric validation.
 * No availability/generation/chat calls; optional provider SDKs remain lazy.
 */
import axios from 'axios';
import { AbstractLLM, DEFAULT_CONFIG } from '../llm/base';
import { OllamaLLM } from '../llm/ollama';
import { OpenAILLM } from '../llm/openai';
import { AnthropicLLM } from '../llm/anthropic';
import { LLMFactory } from '../llm/factory';
import type { BaseLLMConfig } from '../llm/types';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

class ConfigProbeLLM extends AbstractLLM {
    readonly provider = 'fixture';
    readonly model = 'fixture-model';

    async isAvailable(): Promise<boolean> { return false; }
    async generate(): Promise<string> { return ''; }
    async chat(): Promise<string> { return ''; }

    capturedConfig(): Required<BaseLLMConfig> { return this.config; }
}

const malformedNumericValues: Array<[string, unknown]> = [
    ['NaN', NaN],
    ['Infinity', Infinity],
    ['negative Infinity', -Infinity],
    ['negative integer', -1],
    ['negative fraction', -0.1],
    ['numeric string', '0'],
    ['boolean', false],
    ['null', null],
    ['undefined', undefined],
    ['record', {}],
    ['array', []],
];

const nonRecordConfigs: Array<[string, unknown]> = [
    ['null', null],
    ['boolean', false],
    ['number', 0],
    ['string', 'fixture-config'],
    ['array', []],
    ['date', new Date(0)],
    ['map', new Map()],
    ['set', new Set()],
    ['function', () => 'fixture-config'],
];

describe('round5 base LLM configuration', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    afterEach(() => {
        expect(mockedAxios.get).not.toHaveBeenCalled();
        expect(mockedAxios.post).not.toHaveBeenCalled();
        expect(mockedAxios.request).not.toHaveBeenCalled();
    });

    it('accepts an Ollama model string without treating it as a number', () => {
        expect(() => new OllamaLLM({ model: 'fixture-model' })).not.toThrow();
        expect(new OllamaLLM({ model: 'fixture-model' }).model).toBe('fixture-model');
    });

    it('accepts an OpenAI API key string without treating it as a number', () => {
        expect(() => new OpenAILLM({ apiKey: 'offline-fixture-key' })).not.toThrow();
    });

    it('accepts an Anthropic API key string without treating it as a number', () => {
        expect(() => new AnthropicLLM({ apiKey: 'offline-fixture-key' })).not.toThrow();
    });

    it('preserves documented Ollama string fields alongside numeric overrides', () => {
        const config = {
            model: 'fixture-model', baseUrl: 'https://fixture.invalid/ollama',
            temperature: 0.25, maxTokens: 64, timeout: 5,
        };
        expect(() => new OllamaLLM(config)).not.toThrow();
        expect(new OllamaLLM(config)).toMatchObject({
            model: config.model, baseUrl: config.baseUrl,
        });
    });

    it('preserves documented OpenAI string fields alongside numeric overrides', () => {
        const config = {
            model: 'fixture-model', apiKey: 'offline-fixture-key',
            baseUrl: 'https://fixture.invalid/openai', organization: 'fixture-org',
            temperature: 0.25, maxTokens: 64, timeout: 5,
        };
        expect(() => new OpenAILLM(config)).not.toThrow();
        expect(new OpenAILLM(config)).toMatchObject({
            model: config.model, apiKey: config.apiKey,
            baseUrl: config.baseUrl, organization: config.organization,
        });
    });

    it('preserves documented Anthropic string fields alongside numeric overrides', () => {
        const config = {
            model: 'fixture-model', apiKey: 'offline-fixture-key',
            baseUrl: 'https://fixture.invalid/anthropic',
            temperature: 0.25, maxTokens: 64, timeout: 5,
        };
        expect(() => new AnthropicLLM(config)).not.toThrow();
        expect(new AnthropicLLM(config)).toMatchObject({
            model: config.model, apiKey: config.apiKey, baseUrl: config.baseUrl,
        });
    });

    it.each([
        ['fixture-model', 'ollama'],
        ['ollama/fixture-model', 'ollama'],
        ['openai/fixture-model', 'openai'],
        ['anthropic/fixture-model', 'anthropic'],
        ['OpenAI/fixture-model', 'openai'],
    ])('constructs %s offline through fromString', (spec, provider) => {
        expect(() => LLMFactory.fromString(spec)).not.toThrow();
        const llm = LLMFactory.fromString(spec);
        expect(llm.provider).toBe(provider);
        expect(llm.model).toBe('fixture-model');
    });

    it('retains captured provider fields without altering the caller record', () => {
        const config = {
            temperature: 0.25, maxTokens: 64, timeout: 5,
            model: 'fixture-model', apiKey: 'offline-fixture-key',
            baseUrl: 'https://fixture.invalid/v1', organization: 'fixture-org',
        };
        let llm: ConfigProbeLLM | undefined;
        expect(() => { llm = new ConfigProbeLLM(config); }).not.toThrow();
        expect(llm?.capturedConfig()).toEqual(config);
        expect(llm?.capturedConfig()).not.toBe(config);
        expect(Object.isFrozen(llm?.capturedConfig())).toBe(true);
        expect(Object.isFrozen(config)).toBe(false);
        config.temperature = 0.75;
        config.model = 'changed-model';
        expect(llm?.capturedConfig()).toMatchObject({
            temperature: 0.25, model: 'fixture-model',
        });
    });

    it('keeps omitted configuration and omitted numeric fields at their defaults', () => {
        expect(new ConfigProbeLLM().capturedConfig()).toEqual(DEFAULT_CONFIG);
        expect(new ConfigProbeLLM(undefined).capturedConfig()).toEqual(DEFAULT_CONFIG);
        expect(new ConfigProbeLLM({ temperature: 0.25 }).capturedConfig()).toEqual({
            ...DEFAULT_CONFIG, temperature: 0.25,
        });
    });

    it('keeps the existing finite nonnegative numeric range including zero', () => {
        const config = { temperature: 0, maxTokens: 0, timeout: 0 };
        expect(() => new ConfigProbeLLM(config)).not.toThrow();
        expect(new ConfigProbeLLM(config).capturedConfig()).toEqual(config);
    });

    describe.each(['temperature', 'maxTokens', 'timeout'] as const)('%s validation', field => {
        it.each(malformedNumericValues)('rejects %s with the exact fixed code', (_label, value) => {
            const config = { [field]: value } as unknown as BaseLLMConfig;
            expect(() => new ConfigProbeLLM(config)).toThrow(/^JUDGE_CONFIG_INVALID$/);
            expect(() => new OllamaLLM({ ...config, model: 'fixture-model' }))
                .toThrow(/^JUDGE_CONFIG_INVALID$/);
            expect(() => new OpenAILLM({ ...config, apiKey: 'offline-fixture-key' }))
                .toThrow(/^JUDGE_CONFIG_INVALID$/);
            expect(() => new AnthropicLLM({ ...config, apiKey: 'offline-fixture-key' }))
                .toThrow(/^JUDGE_CONFIG_INVALID$/);
        });
    });

    it.each(nonRecordConfigs)('rejects a %s configuration with the exact fixed code', (_label, value) => {
        const config = value as BaseLLMConfig;
        expect(() => new ConfigProbeLLM(config)).toThrow(/^JUDGE_CONFIG_INVALID$/);
        expect(() => new OllamaLLM(config)).toThrow(/^JUDGE_CONFIG_INVALID$/);
        expect(() => new OpenAILLM(config)).toThrow(/^JUDGE_CONFIG_INVALID$/);
        expect(() => new AnthropicLLM(config)).toThrow(/^JUDGE_CONFIG_INVALID$/);
    });
});
