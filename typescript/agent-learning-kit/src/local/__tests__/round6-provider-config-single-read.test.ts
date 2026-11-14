import type { AbstractLLM } from '../llm/base.js';
import { AnthropicLLM } from '../llm/anthropic.js';
import { LLMFactory } from '../llm/factory.js';
import { OllamaLLM } from '../llm/ollama.js';
import { OpenAILLM } from '../llm/openai.js';

type FixtureConfig = {
    model?: string;
    baseUrl?: string;
    apiKey?: string;
    organization?: string;
    temperature?: number;
    maxTokens?: number;
    timeout?: number;
};

type ProviderCase = {
    name: string;
    create: (config?: FixtureConfig) => AbstractLLM;
    defaultModel: string;
    defaultBaseUrl: string;
    credentialEnv?: 'OPENAI_API_KEY' | 'ANTHROPIC_API_KEY';
};

const PROVIDERS: ProviderCase[] = [
    {
        name: 'ollama', create: config => new OllamaLLM(config),
        defaultModel: 'llama3.2', defaultBaseUrl: 'http://localhost:11434',
    },
    {
        name: 'openai', create: config => new OpenAILLM(config),
        defaultModel: 'gpt-4o-mini', defaultBaseUrl: 'https://api.openai.com/v1',
        credentialEnv: 'OPENAI_API_KEY',
    },
    {
        name: 'anthropic', create: config => new AnthropicLLM(config),
        defaultModel: 'claude-3-5-sonnet-20241022', defaultBaseUrl: 'https://api.anthropic.com',
        credentialEnv: 'ANTHROPIC_API_KEY',
    },
];
const FIRST = {
    model: 'fixture-model-first',
    baseUrl: 'http://fixture.invalid/v1',
    apiKey: 'fixture-key-first',
    organization: 'fixture-organization-first',
};
const FIELDS = ['model', 'baseUrl', 'apiKey', 'organization'] as const;
const NUMERIC_FIELDS = ['temperature', 'maxTokens', 'timeout'] as const;
const NUMERIC = { temperature: 0.25, maxTokens: 64, timeout: 9 };
const PRIVATE_MARKER = 'SYNTHETIC_PRIVATE_MARKER';

function fixtureConfig(): FixtureConfig {
    return { ...FIRST, ...NUMERIC };
}

// Inspect constructor state only: never call availability, chat, generate or SDK clients.
function expectCapturedFields(llm: AbstractLLM, provider: ProviderCase): void {
    expect(llm.model).toBe(FIRST.model);
    expect(Reflect.get(llm, 'baseUrl')).toBe(FIRST.baseUrl);
    expect(Reflect.get(llm, 'config')).toMatchObject({ ...FIRST, ...NUMERIC });
    if (provider.credentialEnv) {
        expect(Reflect.get(llm, 'apiKey')).toBe(FIRST.apiKey);
        expect(Reflect.get(llm, '_client')).toBeNull();
    }
    if (provider.name === 'openai') {
        expect(Reflect.get(llm, 'organization')).toBe(FIRST.organization);
    }
}

function withCredentialEnvironment(provider: ProviderCase, value: string | undefined, run: () => void): void {
    const name = provider.credentialEnv;
    if (!name) {
        run();
        return;
    }
    const previous = process.env[name];
    try {
        if (value === undefined) delete process.env[name];
        else process.env[name] = value;
        run();
    } finally {
        if (previous === undefined) delete process.env[name];
        else process.env[name] = previous;
    }
}

describe('round6 provider config single-read boundary (SEC-W05-R5-04)', () => {
    for (const provider of PROVIDERS) {
        describe(provider.name, () => {
            for (const field of FIELDS) {
                it(`uses the first ${field} value and reads it exactly once`, () => {
                    const config = fixtureConfig();
                    let reads = 0;
                    Object.defineProperty(config, field, {
                        enumerable: true,
                        get: () => ++reads === 1 ? FIRST[field] : `changed-${field}`,
                    });
                    const llm = provider.create(config);
                    expect(reads).toBe(1);
                    expectCapturedFields(llm, provider);
                });

                it(`never invokes a throwing second ${field} read`, () => {
                    const config = fixtureConfig();
                    let reads = 0;
                    Object.defineProperty(config, field, {
                        enumerable: true,
                        get: () => {
                            if (++reads > 1) throw new Error(PRIVATE_MARKER);
                            return FIRST[field];
                        },
                    });
                    const llm = provider.create(config);
                    expect(reads).toBe(1);
                    expectCapturedFields(llm, provider);
                });

                it(`maps a throwing first ${field} read to the fixed config diagnostic`, () => {
                    const config = fixtureConfig();
                    let reads = 0;
                    Object.defineProperty(config, field, {
                        enumerable: true,
                        get: () => {
                            reads++;
                            throw new Error(PRIVATE_MARKER);
                        },
                    });
                    expect(() => provider.create(config)).toThrow(new Error('JUDGE_CONFIG_INVALID'));
                    expect(reads).toBe(1);
                });
            }

            it('preserves ordinary config and isolates subsequent caller mutation', () => {
                withCredentialEnvironment(provider, 'fixture-env-key', () => {
                    const config = fixtureConfig();
                    const llm = provider.create(config);
                    for (const field of FIELDS) config[field] = `mutated-${field}`;
                    for (const field of NUMERIC_FIELDS) config[field] = 999;
                    expectCapturedFields(llm, provider);
                });
            });

            it('preserves omitted-config provider and numeric defaults', () => {
                withCredentialEnvironment(provider, undefined, () => {
                    const llm = provider.create();
                    expect(llm.model).toBe(provider.defaultModel);
                    expect(Reflect.get(llm, 'baseUrl')).toBe(provider.defaultBaseUrl);
                    expect(Reflect.get(llm, 'config')).toEqual({ temperature: 0, maxTokens: 1024, timeout: 120 });
                    if (provider.credentialEnv) {
                        expect(Reflect.get(llm, 'apiKey')).toBe('');
                        expect(Reflect.get(llm, '_client')).toBeNull();
                    }
                    if (provider.name === 'openai') expect(Reflect.get(llm, 'organization')).toBeUndefined();
                });
            });

            for (const field of NUMERIC_FIELDS) {
                for (const invalid of [-1, Number.NaN, Number.POSITIVE_INFINITY]) {
                    it(`still rejects ${field}=${String(invalid)}`, () => {
                        const config = fixtureConfig();
                        config[field] = invalid;
                        expect(() => provider.create(config)).toThrow(new Error('JUDGE_CONFIG_INVALID'));
                    });
                }
            }

            it('still accepts zero for every base numeric field', () => {
                const llm = provider.create({ ...fixtureConfig(), temperature: 0, maxTokens: 0, timeout: 0 });
                expect(Reflect.get(llm, 'config')).toMatchObject({ temperature: 0, maxTokens: 0, timeout: 0 });
            });

            it('still constructs through LLMFactory.fromString without initializing a client', () => {
                withCredentialEnvironment(provider, 'fixture-env-key', () => {
                    const llm = LLMFactory.fromString(`${provider.name}/fixture-factory-model`);
                    expect(llm.provider).toBe(provider.name);
                    expect(llm.model).toBe('fixture-factory-model');
                    expect(Reflect.get(llm, 'baseUrl')).toBe(provider.defaultBaseUrl);
                    if (provider.credentialEnv) {
                        expect(Reflect.get(llm, 'apiKey')).toBe('fixture-env-key');
                        expect(Reflect.get(llm, '_client')).toBeNull();
                    }
                });
            });

            if (provider.credentialEnv) {
                for (const environment of [undefined, 'fixture-env-key']) {
                    it(`selects the captured empty key's fallback with environment ${environment === undefined ? 'absent' : 'present'}`, () => {
                        withCredentialEnvironment(provider, environment, () => {
                            const config = fixtureConfig();
                            let reads = 0;
                            Object.defineProperty(config, 'apiKey', {
                                enumerable: true,
                                get: () => ++reads === 1 ? '' : 'fixture-key-later',
                            });
                            const llm = provider.create(config);
                            expect(reads).toBe(1);
                            expect(Reflect.get(llm, 'config').apiKey).toBe('');
                            expect(Reflect.get(llm, 'apiKey')).toBe(environment || '');
                            expect(Reflect.get(llm, '_client')).toBeNull();
                        });
                    });
                }
            }
        });
    }

    it('still defaults a bare factory model specification to Ollama', () => {
        const llm = LLMFactory.fromString('fixture-factory-model');
        expect(llm.provider).toBe('ollama');
        expect(llm.model).toBe('fixture-factory-model');
    });
});
