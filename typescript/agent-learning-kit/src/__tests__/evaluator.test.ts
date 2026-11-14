import { Evaluator } from '../evaluator';
import { BatchRunResult } from '../types';
import { HttpMethod, InvalidAuthError } from '../core';
import { Templates } from '../templates';

import axios from 'axios';
import { __clearRegistryCache } from '../core/cloudRegistry.js';

jest.mock('axios');
const previousRegistrySwitch = process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
beforeAll(() => { delete process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY; });
afterAll(() => {
    if (previousRegistrySwitch === undefined) delete process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY;
    else process.env.AGENT_LEARNING_DISABLE_CLOUD_REGISTRY = previousRegistrySwitch;
});

// Mock the entire APIKeyAuth class from the SDK to spy on the 'request' method
jest.mock('../core', () => {
    const originalModule = jest.requireActual('../core');
    return {
        ...originalModule,
        APIKeyAuth: class {
            baseUrl = 'https://api.futureagi.com';
            _defaultTimeout = 200;
            request = jest.fn();
            constructor(options?: any) {}
        },
    };
});


describe('Evaluator', () => {
    let evaluator: Evaluator;
    let mockRequest: jest.Mock;

    beforeEach(() => {
        __clearRegistryCache();
        (axios.get as jest.Mock).mockReset().mockResolvedValue({ data: { result:
            [...new Set(['factual_accuracy', ...Object.values(Templates).map(template => template.eval_name)])]
                .map(name => ({ name, config: { required_keys: ['query', 'response'] } }))
        } });
        evaluator = new Evaluator({ fiApiKey: 'fixture', fiSecretKey: 'fixture' });
        // Get the mock instance of the request method for each test
        mockRequest = (evaluator as any).request;
        mockRequest.mockClear();
    });

    describe('constructor', () => {
        it('should initialize correctly', () => {
            expect(evaluator).toBeInstanceOf(Evaluator);
        });
    });

    describe('evaluate', () => {
        const mockInputs = { query: ["test query"], response: ["test response"] };
        const mockBatchResult: BatchRunResult = {
            eval_results: [{
                name: 'factual_accuracy',
                output: 'pass',
                output_type: 'Pass/Fail',
                eval_id: 'test-eval-id',
                reason: "",
                runtime: 100,
            }]
        };

        it('should perform a successful evaluation', async () => {
            mockRequest.mockResolvedValue(mockBatchResult);

            const result = await evaluator.evaluate('factual_accuracy', mockInputs, { modelName: 'test-model' });

            expect(mockRequest).toHaveBeenCalledTimes(1);

            const callArgs = mockRequest.mock.calls[0];
            expect(callArgs[0]).toEqual({
                method: HttpMethod.POST,
                url: 'https://api.futureagi.com/sdk/api/v1/new-eval/',
                json: {
                    eval_name: 'factual_accuracy',
                    inputs: mockInputs,
                    trace_eval: false,
                    custom_eval_name: undefined,
                    model: 'test-model',
                    span_id: undefined,
                    is_async: false,
                    error_localizer: false
                },
                timeout: NaN
            });
            expect(typeof callArgs[1]).toBe('function');
            expect(result).toEqual({
                eval_results: mockBatchResult.eval_results.map(row => ({ ...row, failure: false, reason: 'EVALUATION_COMPLETED' })),
                warnings: [], status: 'completed', reasonCode: 'EVALUATION_COMPLETED',
            });
            expect(axios.get).toHaveBeenCalledTimes(1);
        });

        it('should throw an error for invalid eval_templates', async () => {
            await expect(evaluator.evaluate({} as any, mockInputs, { modelName: 'test-model' })).rejects.toThrow(
                'Unsupported eval_templates argument.'
            );
        });

        it('should correctly transform inputs for the API payload when provided as a dict of strings', async () => {
            mockRequest.mockResolvedValue(mockBatchResult);
            const singleInput = { query: "q1", response: "r1" };

            await evaluator.evaluate('factual_accuracy', singleInput, { modelName: 'test-model' });

            expect(mockRequest).toHaveBeenCalledWith(
                expect.objectContaining({
                    json: expect.objectContaining({
                        inputs: {
                            query: ["q1"],
                            response: ["r1"]
                        }
                    })
                }),
                expect.any(Function)
            );
        });
    });

    describe('list_evaluations', () => {
        it('should return a list of evaluations', async () => {
            const mockEvalList = [{ name: 'factual_accuracy', id: '1' }, { name: 'toxicity', id: '2' }];
            mockRequest.mockResolvedValue(mockEvalList);

            const result = await evaluator.list_evaluations();

            expect(mockRequest).toHaveBeenCalledWith(
                expect.objectContaining({
                    method: HttpMethod.GET,
                    url: expect.stringContaining('/get-evals'),
                }),
                expect.any(Function) // EvalInfoResponseHandler
            );
            expect(result).toEqual(mockEvalList);
        });
    });

    describe('_get_eval_info caching', () => {
        it('should cache the results of _get_eval_info', async () => {
            const mockEvalList = [
                { name: 'factual_accuracy', eval_id: '1' },
                { name: 'toxicity', eval_id: '2' },
            ];
            mockRequest.mockResolvedValue(mockEvalList);

            // Access the private method for testing purposes
            const getEvalInfo = (evaluator as any)._get_eval_info.bind(evaluator);

            // First call
            const result1 = await getEvalInfo('factual_accuracy');
            expect(result1).toEqual(mockEvalList[0]);
            expect(mockRequest).toHaveBeenCalledTimes(1);

            // Second call - should hit the cache
            const result2 = await getEvalInfo('factual_accuracy');
            expect(result2).toEqual(mockEvalList[0]);
            expect(mockRequest).toHaveBeenCalledTimes(1); // Should not be called again

            // Third call with a different eval_name - should make a new request
            const result3 = await getEvalInfo('toxicity');
            expect(result3).toEqual(mockEvalList[1]);
            expect(mockRequest).toHaveBeenCalledTimes(2);
        });
    });

    describe('evaluate every available eval_name', () => {
        // Collect all eval_name strings defined in Templates
        const evalNames = Object.values(Templates).map(t => t.eval_name);

        const dummyBatchResult: BatchRunResult = {
            eval_results: [{
                name: 'dummy',
                output: 'pass',
                output_type: 'Pass/Fail',
                eval_id: 'dummy-id',
                reason: "",
                runtime: 0,
            }]
        };

        it.each(evalNames)('should construct payload for %s', async (evalName) => {
            mockRequest.mockResolvedValue(dummyBatchResult);

            await evaluator.evaluate(evalName, { input: 'example', query: 'example', response: 'example' }, { modelName: 'test-model' });

            expect(mockRequest).toHaveBeenCalledWith(
                expect.objectContaining({
                    json: expect.objectContaining({
                        eval_name: evalName
                    })
                }),
                expect.any(Function)
            );
        });
    });
});
