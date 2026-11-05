import * as ts from 'typescript';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// This test reads source; it never imports a new production helper on the RED
// baseline. It is deliberately an assertion failure when boundaries are absent.
const contracts: Array<[string, string, string[]]> = [
    ['core/cloudRegistry.ts', 'loadRegistry', ['opts']],
    ['core/cloudRegistry.ts', 'mapInputsToBackend', ['userInputs', 'opts']],
    ['core/cloudRegistry.ts', 'mapInputsToBackendSync', ['userInputs']],
    ['evaluator.ts', 'evaluate', ['evalTemplates', 'inputs', 'options']],
    ['evaluator.ts', 'submit', ['evalTemplates', 'inputs', 'options']],
    ['evaluator.ts', 'evaluatePipeline', ['evalData']],
    ['evaluator.ts', 'getPipelineResults', ['versions']],
    ['protect.ts', 'protect', ['protectRules']],
    ['local/autoeval/pipeline.ts', 'evaluate', ['input', 'configOverrides']],
    ['local/autoeval/pipeline.ts', 'analyzeOnly', ['input']],
    ['local/autoeval/pipeline.ts', 'configure', ['config']],
    ['local/scanner/scanner.ts', 'scan', ['config']],
    ['local/scanner/scanner.ts', 'addRule', ['rule']],
    ['local/streaming/evaluator.ts', 'constructor', ['config']],
    ['local/llm/base.ts', 'validateJudgeResult', ['parsed']],
    ['local/llm/base.ts', 'batchJudge', ['evaluations']],
    ['local/metrics/code/index.ts', 'allSecurityChecks', ['input', 'config']],
    ['local/metrics/code/sql-injection.ts', 'sqlInjection', ['input', 'config']],
    ['local/metrics/code/xss-detection.ts', 'xssDetection', ['input', 'config']],
    ['local/metrics/code/secrets-detection.ts', 'secretsDetection', ['input', 'config']],
    ['local/metrics/code/types.ts', 'createSecurityResult', ['issues', 'config']],
    ['local/metrics/rag/answer-relevance.ts', 'answerRelevance', ['input', 'config']],
    ['local/metrics/rag/context-recall.ts', 'contextRecall', ['input', 'config']],
    ['local/metrics/rag/context-relevance.ts', 'contextRelevance', ['input', 'config']],
    ['local/metrics/rag/context-relevance.ts', 'contextUtilization', ['input', 'config']],
    ['local/metrics/rag/faithfulness.ts', 'faithfulness', ['input', 'config']],
    ['local/metrics/hallucination/detection.ts', 'hallucinationDetection', ['input', 'config']],
    ['local/metrics/json.ts', 'jsonSchema', ['config']],
    ...['bleuScore', 'rougeScore', 'recallScore', 'levenshteinSimilarity', 'numericSimilarity', 'semanticListContains']
        .map(name => ['local/metrics/similarity.ts', name, ['config']] as [string, string, string[]]),
];
const captureNames = new Set(['snapshotBoundary', 'snapshotConfig']);
function isCapture(node: ts.Node): node is ts.CallExpression {
    if (!ts.isCallExpression(node)) return false;
    const name = ts.isIdentifier(node.expression) ? node.expression.text :
        ts.isPropertyAccessExpression(node.expression) ? node.expression.name.text : '';
    return captureNames.has(name);
}
function identifiers(node: ts.Node, name: string): ts.Identifier[] {
    const result: ts.Identifier[] = [];
    const visit = (child: ts.Node) => {
        const parent = child.parent;
        const propertyName = parent && ((ts.isPropertyAccessExpression(parent) && parent.name === child) ||
            (ts.isPropertyAssignment(parent) && parent.name === child));
        if (ts.isIdentifier(child) && child.text === name && !propertyName) result.push(child);
        ts.forEachChild(child, visit);
    };
    visit(node); return result;
}

describe('W04 C5 source boundary enforcement', () => {
    test.each(contracts)('R3-AST %s %s owns all object parameters before downstream access', (file, name, parameters) => {
        const source = ts.createSourceFile(file, readFileSync(resolve(__dirname, '../../', file), 'utf8'), ts.ScriptTarget.Latest, true);
        const bodies: ts.Block[] = [];
        const visit = (node: ts.Node) => {
            if ((ts.isMethodDeclaration(node) || ts.isFunctionDeclaration(node) || ts.isConstructorDeclaration(node)) && node.body) {
                const label = ts.isConstructorDeclaration(node) ? 'constructor' : node.name?.getText(source);
                if (label === name) bodies.push(node.body);
            }
            ts.forEachChild(node, visit);
        };
        visit(source);
        expect(bodies.length).toBeGreaterThan(0);
        for (const body of bodies) for (const parameter of parameters) {
            const reads = identifiers(body, parameter);
            const captures: ts.CallExpression[] = [];
            const gather = (node: ts.Node) => {
                if (isCapture(node) && node.arguments.some(argument => identifiers(argument, parameter).length > 0)) captures.push(node);
                ts.forEachChild(node, gather);
            };
            gather(body);
            expect(captures.length).toBeGreaterThan(0);
            const capture = captures[0];
            if (!capture) continue;
            const assignment = capture.parent;
            const reassigned = ts.isBinaryExpression(assignment) && assignment.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
                ((ts.isIdentifier(assignment.left) && assignment.left.text === parameter) ||
                    (ts.isObjectLiteralExpression(assignment.left) && identifiers(assignment.left, parameter).length === 1));
            const violations = reads.filter(read => {
                // Parameter occurrence inside the one capture call is allowed.
                if (read.pos >= capture.pos && read.end <= capture.end) return false;
                if (reassigned && read.pos >= assignment.left.pos && read.end <= assignment.left.end) return false;
                // Rebinding kills the original object; downstream uses refer to
                // the frozen record. Without rebinding, every subsequent use of
                // the original identifier (including an alias) is a violation.
                return !(reassigned && read.pos >= assignment.end);
            });
            expect(violations.map(node => source.getLineAndCharacterOfPosition(node.getStart()).line + 1)).toEqual([]);
        }
    });
});
