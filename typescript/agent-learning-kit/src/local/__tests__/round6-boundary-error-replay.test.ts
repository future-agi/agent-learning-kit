import { Evaluator } from '../../evaluator.js';
import { analyzeContent } from '../autoeval/analyzer.js';
import { selectMetrics } from '../autoeval/selector.js';
import { snapshotBoundary } from '../autoeval/types.js';
import { StreamingEvaluator } from '../streaming/evaluator.js';

const PRIVATE_MARKER = 'SYNTHETIC_BOUNDARY_REPLAY_MARKER';

type Observations = { message: number; prototype: number; cause: number; requests: number };
const observations = (): Observations => ({ message: 0, prototype: 0, cause: 0, requests: 0 });

function throwingIterator<T extends object>(record: T, error: unknown): T {
    Object.defineProperty(record, Symbol.iterator, {
        get() { throw error; },
    });
    return record;
}

async function rejected(action: () => unknown): Promise<unknown> {
    try { await action(); } catch (error) { return error; }
    throw new Error('EXPECTED_BOUNDARY_FAILURE');
}

function expectFixedError(error: unknown, code: string): asserts error is Error {
    expect(error).toBeInstanceOf(Error);
    const message = Object.getOwnPropertyDescriptor(error as Error, 'message');
    expect(message?.get).toBeUndefined();
    expect(message?.set).toBeUndefined();
    expect(message?.value).toBe(code);
    expect((error as Error).message).toBe(code);
    expect((error as Error).name).toBe('Error');
    expect('cause' in (error as Error)).toBe(false);
    expect((error as Error).stack ?? '').not.toContain(PRIVATE_MARKER);
}

type Boundary = {
    name: string;
    code: string;
    invoke: (error: unknown, observed: Observations) => unknown;
};

const boundaries: Boundary[] = [
    {
        name: 'analyzeContent',
        code: 'EVALUATION_INPUT_INVALID',
        invoke: error => analyzeContent(throwingIterator({ response: 'A plain response.' }, error)),
    },
    {
        name: 'selectMetrics',
        code: 'EVALUATION_CONFIG_INVALID',
        invoke: error => {
            const input = { response: 'A plain response.' };
            return selectMetrics(analyzeContent(input), input, throwingIterator({}, error));
        },
    },
    {
        name: 'StreamingEvaluator construction',
        code: 'STREAM_CONFIG_INVALID',
        invoke: error => new StreamingEvaluator(throwingIterator({ metrics: ['exact_match'] }, error)),
    },
    {
        name: 'Evaluator input capture',
        code: 'EVALUATION_CONFIG_INVALID',
        invoke: (error, observed) => {
            // Exercise the public method's input boundary without constructing an
            // authenticated client, reading environment credentials, or transport.
            const evaluator = Object.create(Evaluator.prototype) as Evaluator;
            Object.defineProperty(evaluator, 'request', {
                value: () => {
                    observed.requests++;
                    throw new Error('UNEXPECTED_TRANSPORT');
                },
            });
            return evaluator.evaluate('exact_match', throwingIterator({ response: 'A plain response.' }, error));
        },
    },
];

const mutations: Array<[string, (error: Error, observed: Observations) => void]> = [
    ['an unchanged retained instance', () => {}],
    ['a rewritten message and attached cause', error => {
        error.message = PRIVATE_MARKER;
        Object.defineProperty(error, 'cause', { value: { detail: PRIVATE_MARKER }, configurable: true });
    }],
    ['a replaced hostile prototype', (error, observed) => {
        Object.setPrototypeOf(error, new Proxy(Object.create(null), {
            getPrototypeOf() {
                observed.prototype++;
                throw new Error(PRIVATE_MARKER);
            },
            get() {
                observed.prototype++;
                throw new Error(PRIVATE_MARKER);
            },
        }));
    }],
    ['a throwing message accessor', (error, observed) => {
        Object.defineProperty(error, 'message', {
            configurable: true,
            get() {
                observed.message++;
                throw new Error(PRIVATE_MARKER);
            },
        });
    }],
    ['a throwing cause accessor', (error, observed) => {
        Object.defineProperty(error, 'cause', {
            configurable: true,
            get() {
                observed.cause++;
                throw new Error(PRIVATE_MARKER);
            },
        });
    }],
];

function invalidNestedPattern(): unknown {
    return snapshotBoundary({ outer: { customPatterns: [() => undefined] } }, 'EVALUATION_CONFIG_INVALID');
}

for (const boundary of boundaries) {
    describe(`round6 boundary replay: ${boundary.name}`, () => {
        it('keeps ordinary failures fixed and never reaches transport', async () => {
            const observed = observations();
            const error = await rejected(() => boundary.invoke(new Error(PRIVATE_MARKER), observed));
            expectFixedError(error, boundary.code);
            expect(observed).toEqual(observations());
        });

        it.each(mutations)('reconstructs %s from its private reason', async (_label, mutate) => {
            const observed = observations();
            const retained = await rejected(() => boundary.invoke(new Error(PRIVATE_MARKER), observed));
            expectFixedError(retained, boundary.code);
            mutate(retained, observed);

            const replayed = await rejected(() => boundary.invoke(retained, observed));
            expect(observed).toEqual(observations());
            expect(replayed).not.toBe(retained);
            expectFixedError(replayed, boundary.code);

            // Even an unmodified fresh failure can escape and be replayed again.
            const repeated = await rejected(() => boundary.invoke(replayed, observed));
            expect(observed).toEqual(observations());
            expect(repeated).not.toBe(replayed);
            expect(repeated).not.toBe(retained);
            expectFixedError(repeated, boundary.code);
        });

        it.each(mutations)('preserves a nested security reason despite %s', async (_label, mutate) => {
            const observed = observations();
            const retained = await rejected(invalidNestedPattern);
            expectFixedError(retained, 'INVALID_SECURITY_PATTERN');
            mutate(retained, observed);

            const replayed = await rejected(() => boundary.invoke(retained, observed));
            expect(observed).toEqual(observations());
            expect(replayed).not.toBe(retained);
            expectFixedError(replayed, 'INVALID_SECURITY_PATTERN');
        });
    });
}

describe('round6 private boundary reason controls', () => {
    it('preserves a nested customPatterns rejection through outer capture frames', async () => {
        const error = await rejected(invalidNestedPattern);
        expectFixedError(error, 'INVALID_SECURITY_PATTERN');
    });

    it('preserves the reason for an unreadable nested customPatterns field', async () => {
        const nested = Object.defineProperty({}, 'customPatterns', {
            get() { throw new Error(PRIVATE_MARKER); },
        });
        const error = await rejected(() => snapshotBoundary({ nested }, 'EVALUATION_CONFIG_INVALID'));
        expectFixedError(error, 'INVALID_SECURITY_PATTERN');
    });

    it('does not inspect an untrusted thrown proxy to classify its reason', async () => {
        const observed = observations();
        const foreign = new Proxy({}, {
            getPrototypeOf() {
                observed.prototype++;
                throw new Error(PRIVATE_MARKER);
            },
            get() {
                observed.message++;
                throw new Error(PRIVATE_MARKER);
            },
        });
        const error = await rejected(() => snapshotBoundary(throwingIterator({}, foreign), 'EVALUATION_INPUT_INVALID'));
        expect(observed).toEqual(observations());
        expectFixedError(error, 'EVALUATION_INPUT_INVALID');
    });

    it('does not trust a forged BoundaryError prototype or public message', async () => {
        const retained = await rejected(invalidNestedPattern);
        expectFixedError(retained, 'INVALID_SECURITY_PATTERN');
        const forged = Object.create(Object.getPrototypeOf(retained));
        Object.defineProperty(forged, 'message', { value: 'INVALID_SECURITY_PATTERN' });
        const error = await rejected(() => analyzeContent(throwingIterator({ response: 'A plain response.' }, forged)));
        expect(error).not.toBe(forged);
        expectFixedError(error, 'EVALUATION_INPUT_INVALID');
    });

    it('does not register caller-chosen reasons through an escaped error constructor', async () => {
        const retained = await rejected(invalidNestedPattern);
        expectFixedError(retained, 'INVALID_SECURITY_PATTERN');
        const EscapedConstructor = retained.constructor as new (message: string) => Error;
        const forged = new EscapedConstructor(PRIVATE_MARKER);
        const error = await rejected(() => analyzeContent(throwingIterator({ response: 'A plain response.' }, forged)));
        expect(error).not.toBe(forged);
        expectFixedError(error, 'EVALUATION_INPUT_INVALID');
    });

    it('still captures valid values once into immutable owned records', () => {
        let reads = 0;
        const original = {
            get response() {
                reads++;
                return 'A plain response.';
            },
            metadata: { stable: true },
        };
        const captured = snapshotBoundary(original, 'EVALUATION_INPUT_INVALID');
        expect(reads).toBe(1);
        expect(captured).not.toBe(original);
        expect(captured.response).toBe('A plain response.');
        expect(captured.metadata.stable).toBe(true);
        expect(Object.isFrozen(captured)).toBe(true);
        expect(Object.isFrozen(captured.metadata)).toBe(true);
        expect(snapshotBoundary(captured, 'EVALUATION_INPUT_INVALID')).toBe(captured);
        expect(reads).toBe(1);
    });
});
