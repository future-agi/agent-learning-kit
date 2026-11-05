import { AbstractLLM } from '../llm/base.js';

class FixtureJudge extends AbstractLLM {
    readonly provider = 'fixture';
    readonly model = 'fixture';
    chat = jest.fn<Promise<string>, any[]>().mockResolvedValue('{}');
    async generate(): Promise<string> { return ''; }
    async isAvailable(): Promise<boolean> { return true; }
    validate(value: unknown) { return this.validateJudgeResult(value, 'PRIVATE_SENTINEL'); }
}

describe('W04 judge fail-closed', () => {
    test.each(['unparseable PRIVATE_SENTINEL', '{}', 'null', '{"score":"1"}', '{"score":1e999}', '{"score":1,"passed":"true"}', '{"score":1,"passed":null}', 'Score: 9/10'])('FO-05 rejects malformed judgment %p', async raw => {
        const judge = new FixtureJudge();
        judge.chat.mockResolvedValue(raw);
        const result = await judge.judge('q', 'a', 'criteria');
        expect(result.passed).toBe(false);
        expect(result.score).toBe(0);
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
    });

    test.each([NaN, Infinity, -Infinity])('FO-05 rejects nonfinite direct score %p', score => {
        expect(new FixtureJudge().validate({ score, passed: true }).passed).toBe(false);
    });

    test('FO-05 provider exception is value-free', async () => {
        const judge = new FixtureJudge();
        judge.chat.mockRejectedValue(new Error('PRIVATE_SENTINEL'));
        const result = await judge.batchJudge([{ query: 'q', response: 'a', criteria: 'c' }]);
        expect(result[0].passed).toBe(false);
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
    });

    test('R2-L02 judge snapshots the score once before validation and normalization', () => {
        let reads = 0;
        expect(new FixtureJudge().validate({ get score() { reads++; return 1; }, passed: true }).passed).toBe(true);
        expect(reads).toBe(1);
    });
    test('R2-L01 judge requires canonical primitives and derives only an omitted verdict', () => {
        const judge = new FixtureJudge();
        expect(judge.validate({ score: new Number(1), passed: true }).passed).toBe(false);
        expect(judge.validate({ score: 1, passed: new Boolean(true) }).passed).toBe(false);
        expect(judge.validate({ score: 0.9 }).passed).toBe(true);
        expect(judge.validate({ score: 90, passed: true }).score).toBe(0.9);
    });
    test('FO-05 genuine structured PASS control', async () => {
        const judge = new FixtureJudge();
        judge.chat.mockResolvedValue('{"score":0.9,"passed":true,"reason":"PRIVATE_SENTINEL"}');
        const result = await judge.judge('q', 'a', 'criteria');
        expect(result.score).toBe(0.9);
        expect(result.passed).toBe(true);
        expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL');
    });
});
