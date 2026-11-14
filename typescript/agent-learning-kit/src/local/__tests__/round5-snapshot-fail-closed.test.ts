import { Protect } from '../../protect.js';
import { EvalResponseHandler, Evaluator } from '../../evaluator.js';
import { snapshotBoundary } from '../autoeval/types.js';
import { analyzeContent } from '../autoeval/analyzer.js';
import { selectMetrics } from '../autoeval/selector.js';
import { StreamingEvaluator } from '../streaming/evaluator.js';
import { containsAll } from '../metrics/string.js';

// Synthetic fixtures only. All transport calls are replaced; no credentials or network.
const text = 'The city library opens every morning.';
const placements = ['own', 'inherited'] as const;
type Placement = typeof placements[number];

function installIterator(array: unknown[], placement: Placement, descriptor: PropertyDescriptor): void {
    if (placement === 'own') Object.defineProperty(array, Symbol.iterator, descriptor);
    else Object.setPrototypeOf(array, Object.create(Array.prototype, { [Symbol.iterator]: descriptor }));
}

function hideTail<T>(array: T[], placement: Placement = 'own') {
    const iterator = jest.fn(function* () { yield array[0]; });
    installIterator(array, placement, { value: iterator });
    return { array, iterator };
}

function throwingIterator<T extends object>(record: T, rejected: unknown): T {
    return Object.defineProperty(record, Symbol.iterator, { get() { throw rejected; } });
}

function hostileRejection() {
    const getPrototypeOf = jest.fn(() => { throw new Error('SYNTHETIC_REJECTED_VALUE'); });
    const get = jest.fn(() => { throw new Error('SYNTHETIC_REJECTED_PROPERTY'); });
    return { rejected: new Proxy({}, { getPrototypeOf, get }), getPrototypeOf, get };
}

const protectCases = placements.flatMap(placement => [false, true].map(flash => ({
    id: `${placement}-${flash ? 'flash' : 'standard'}`, placement, flash
})));

describe('round 5 indexed array capture', () => {
    test.each(protectCases)('R5-S01-$id cannot hide a failed Protect row', async ({ placement, flash }) => {
        const { array, iterator } = hideTail([
            { failure: false, data: ['Passed'] },
            { failure: true, data: ['Failed'] }
        ], placement);
        const request = jest.fn().mockResolvedValue({ eval_results: array });
        const client = new Protect({ evaluator: { baseUrl: 'https://unused.invalid', request } as any });
        const result = await client.protect('safe input', [{ metric: 'Toxicity' }], undefined, true, 30000, flash);
        expect(result).toMatchObject({
            status: 'incomplete',
            reasonCode: flash ? 'PROTECT_RESULT_INVALID' : 'PROTECTION_INCOMPLETE',
            coverage: { required: 1, executed: 1, completed: 0, skipped: 0, errored: 1 }
        });
        expect(request).toHaveBeenCalledTimes(1);
        expect(iterator).not.toHaveBeenCalled();
    });

    const parserCases = placements.flatMap(placement => ['result', 'evaluations'].flatMap(container =>
        ['null', 'hole'].map(slot => ({ id: `${placement}-${container}-${slot}`, placement, container, slot }))));
    test.each(parserCases)('R5-S02-$id preserves the invalid parser slot', ({ placement, container, slot }) => {
        const rows: any[] = [{ output: 1, output_type: 'score', failure: false }];
        if (slot === 'null') rows.push(null);
        else rows.length = 2;
        const { iterator } = hideTail(rows, placement);
        const result = EvalResponseHandler._parseSuccess({ data: {
            result: container === 'result' ? rows : [{ evaluations: rows }]
        } } as any);
        expect(result).toMatchObject({ status: 'incomplete', reasonCode: 'EVALUATION_INCOMPLETE' });
        expect(result.eval_results).toHaveLength(2);
        expect(result.eval_results[0]).toMatchObject({ output: 1, failure: false });
        expect(result.eval_results[1]).toMatchObject({ output: null, failure: true, reason: 'EVALUATION_RESULT_INVALID' });
        expect(iterator).not.toHaveBeenCalled();
    });

    test.each(placements)('R5-S03-%s cannot hide a required missing keyword', placement => {
        const { array, iterator } = hideTail(['present', 'missing'], placement);
        expect(containsAll('present', { keywords: array })).toEqual({
            score: 0.5, passed: false, reason: 'KEYWORDS_MISSING'
        });
        expect(iterator).not.toHaveBeenCalled();
    });

    test('R5-S04 holes remain explicit invalid evidence, not inherited values', () => {
        const keywords = ['present'];
        keywords.length = 2;
        Object.setPrototypeOf(keywords, Object.create(Array.prototype, { 1: { value: 'present' } }));
        const captured = snapshotBoundary(keywords, 'METRIC_CONFIG_INVALID');
        expect(captured).toEqual(['present', undefined]);
        expect(Object.prototype.hasOwnProperty.call(captured, 1)).toBe(true);
        expect(containsAll('present', { keywords })).toEqual({ score: 0, passed: false, reason: 'METRIC_CONFIG_INVALID' });
    });

    test.each(placements)('R5-S05-%s does not read an array iterator getter', placement => {
        const array = ['first', 'second'];
        const getter = jest.fn(() => { throw new Error('SYNTHETIC_ITERATOR_GETTER'); });
        installIterator(array, placement, { get: getter });
        expect(snapshotBoundary(array, 'EVALUATION_INPUT_INVALID')).toEqual(['first', 'second']);
        expect(getter).not.toHaveBeenCalled();
    });

    test('R5-S06 array length and each supplied indexed value are captured once', () => {
        const reads = new Map<PropertyKey, number>();
        const array = new Proxy(['first', 'second'], {
            get(target, key, receiver) {
                if (key === 'length' || key === '0' || key === '1') {
                    const count = (reads.get(key) ?? 0) + 1;
                    reads.set(key, count);
                    if (count > 1) throw new Error('SYNTHETIC_SECOND_READ');
                }
                return Reflect.get(target, key, receiver);
            }
        });
        expect(snapshotBoundary(array, 'EVALUATION_INPUT_INVALID')).toEqual(['first', 'second']);
        expect([...reads.entries()]).toEqual([['length', 1], ['0', 1], ['1', 1]]);
    });

    test.each([NaN, Infinity, -1, 1.5, 0x100000000, '2'])('R5-S07 rejects invalid array-proxy length %s', length => {
        const array = new Proxy(['first'], {
            get(target, key, receiver) { return key === 'length' ? length : Reflect.get(target, key, receiver); }
        });
        // In the unfixed code, Array.from could try billions of iterations. The
        // overridden iterator keeps every baseline case bounded and returns a pass.
        Object.defineProperty(array, Symbol.iterator, { value: function* () { yield 'first'; } });
        expect(() => snapshotBoundary(array, 'EVALUATION_INPUT_INVALID')).toThrow(new Error('EVALUATION_INPUT_INVALID'));
    });

    test('R5-S08 ordinary arrays are frozen snapshots with aliases and callback policy intact', () => {
        const member = { value: 'first' };
        const input = [member, member];
        const captured = snapshotBoundary(input, 'EVALUATION_INPUT_INVALID');
        expect(captured).toEqual([{ value: 'first' }, { value: 'first' }]);
        expect(captured).not.toBe(input);
        expect(captured[0]).not.toBe(member);
        expect(captured[0]).toBe(captured[1]);
        expect(Object.isFrozen(captured)).toBe(true);
        expect(Object.isFrozen(captured[0])).toBe(true);
        member.value = 'changed';
        expect(captured[0].value).toBe('first');
        expect(snapshotBoundary(captured, 'EVALUATION_INPUT_INVALID')).toBe(captured);
        expect(containsAll('first second', { keywords: ['first', 'second'] }).passed).toBe(true);
        const callback = () => true;
        expect(snapshotBoundary({ callback }, 'METRIC_CONFIG_INVALID', true).callback).toBe(callback);
        expect(() => snapshotBoundary({ callback }, 'METRIC_CONFIG_INVALID')).toThrow(new Error('METRIC_CONFIG_INVALID'));
    });

    test('R5-S09 deliberately supported non-array iterables still capture all yielded evidence', () => {
        expect(containsAll('present', { keywords: new Set(['present', 'missing']) as unknown as string[] }))
            .toEqual({ score: 0.5, passed: false, reason: 'KEYWORDS_MISSING' });
        let iteratorReads = 0;
        let iterations = 0;
        let memberReads = 0;
        const iterable = {
            get [Symbol.iterator]() {
                iteratorReads++;
                return function* () {
                    iterations++;
                    yield { get value() { memberReads++; return 'first'; } };
                    yield { value: 'second' };
                };
            }
        };
        expect(snapshotBoundary(iterable, 'EVALUATION_INPUT_INVALID')).toEqual([{ value: 'first' }, { value: 'second' }]);
        expect([iteratorReads, iterations, memberReads]).toEqual([1, 1, 1]);
    });

    test.each([false, true])('R5-S10 ordinary one-row Protect control, flash=%s', async flash => {
        const request = jest.fn().mockResolvedValue({ eval_results: [{ failure: false, data: ['Passed'] }] });
        const client = new Protect({ evaluator: { baseUrl: 'https://unused.invalid', request } as any });
        const result = await client.protect('safe input', [{ metric: 'Toxicity' }], undefined, false, 30000, flash);
        expect(result).toMatchObject({ status: 'passed', coverage: { required: 1, executed: 1, completed: 1, skipped: 0, errored: 0 } });
        expect(request).toHaveBeenCalledTimes(1);
    });
});

const syncRoutes: Array<{ id: string; code: string; run: (rejected: unknown) => unknown }> = [
    { id: 'analyzeContent', code: 'EVALUATION_INPUT_INVALID',
        run: rejected => analyzeContent(throwingIterator({ response: text }, rejected)) },
    { id: 'selectMetrics-input', code: 'EVALUATION_CONFIG_INVALID',
        run: rejected => selectMetrics(analyzeContent({ response: text }), throwingIterator({ response: text }, rejected)) },
    { id: 'selectMetrics-config', code: 'EVALUATION_CONFIG_INVALID',
        run: rejected => selectMetrics(analyzeContent({ response: text }), { response: text }, throwingIterator({}, rejected)) },
    { id: 'StreamingEvaluator', code: 'STREAM_CONFIG_INVALID',
        run: rejected => new StreamingEvaluator(throwingIterator({ metrics: ['one_line'] }, rejected)) },
    { id: 'Evaluator-constructor', code: 'EVALUATION_CONFIG_INVALID',
        run: rejected => new Evaluator(throwingIterator({}, rejected)) }
];

describe('round 5 rejected-value identity classification', () => {
    test.each(syncRoutes)('R5-S11-$id does not inspect a thrown proxy', ({ code, run }) => {
        const hostile = hostileRejection();
        expect(() => run(hostile.rejected)).toThrow(new Error(code));
        expect(hostile.getPrototypeOf).not.toHaveBeenCalled();
        expect(hostile.get).not.toHaveBeenCalled();
    });

    test.each(syncRoutes)('R5-S12-$id still fixes Error-valued getter diagnostics', ({ code, run }) => {
        expect(() => run(new Error('SYNTHETIC_GETTER_ERROR'))).toThrow(new Error(code));
    });

    test.each(['inputs', 'options'])('R5-S13 Evaluator %s capture does not inspect a thrown proxy', async slot => {
        const hostile = hostileRejection();
        // No constructor, auth, or transport: capture is the first step of evaluate.
        const evaluator: Evaluator = Object.create(Evaluator.prototype);
        const request = jest.fn();
        evaluator.request = request;
        const inputs = slot === 'inputs' ? throwingIterator({ response: text }, hostile.rejected) : { response: text };
        const options = slot === 'options' ? throwingIterator({}, hostile.rejected) : {};
        await expect(evaluator.evaluate('one_line', inputs, options)).rejects.toThrow(new Error('EVALUATION_CONFIG_INVALID'));
        expect(request).not.toHaveBeenCalled();
        expect(hostile.getPrototypeOf).not.toHaveBeenCalled();
        expect(hostile.get).not.toHaveBeenCalled();
    });

    test('R5-S14 preserves the internally assigned custom-pattern reason through nested catches', () => {
        const hostile = hostileRejection();
        const config = { nested: { customPatterns: throwingIterator({}, hostile.rejected) } };
        expect(() => snapshotBoundary(config, 'METRIC_CONFIG_INVALID')).toThrow(new Error('INVALID_SECURITY_PATTERN'));
        expect(hostile.getPrototypeOf).not.toHaveBeenCalled();
        expect(hostile.get).not.toHaveBeenCalled();
        const getter = Object.defineProperty({}, 'customPatterns', {
            get() { throw new Error('SYNTHETIC_PATTERN_GETTER'); }
        });
        expect(() => snapshotBoundary({ nested: getter }, 'METRIC_CONFIG_INVALID')).toThrow(new Error('INVALID_SECURITY_PATTERN'));
    });

    test('R5-S15 an error prototype does not confer internal boundary-error identity', () => {
        let internal: unknown;
        try { snapshotBoundary(() => true, 'METRIC_CONFIG_INVALID'); } catch (error) { internal = error; }
        expect(internal).toBeInstanceOf(Error);
        const forged = Object.create(Object.getPrototypeOf(internal), { message: { value: 'SYNTHETIC_FORGED_ERROR' } });
        expect(() => analyzeContent(throwingIterator({ response: text }, forged))).toThrow(new Error('EVALUATION_INPUT_INVALID'));
    });

    test('R5-S16 revoked rejected proxies and ordinary field getters also fail with fixed codes', () => {
        const { proxy, revoke } = Proxy.revocable({}, {});
        revoke();
        expect(() => analyzeContent(throwingIterator({ response: text }, proxy))).toThrow(new Error('EVALUATION_INPUT_INVALID'));
        const input = Object.defineProperty({}, 'response', { get() { throw new Error('SYNTHETIC_FIELD_ERROR'); } });
        expect(() => analyzeContent(input as any)).toThrow(new Error('EVALUATION_INPUT_INVALID'));
    });
});
