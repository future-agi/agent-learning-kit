import assert from 'node:assert/strict';
import * as root from '@future-agi/agent-learning-kit';
import {
  Comparator, EvalInfoResponseHandler, EvalResponseHandler, EvalTags, EvalTemplateManager,
  Evaluator, Execution, ExecutionError, ExecutionMode, JsonResultHandler, OutputType,
  Protect, RequiredKeys, Templates, evaluate, evaluate_pipeline, get_eval_result,
  get_pipeline_results, list_evaluations, normalizeStatus, protect,
} from '@future-agi/agent-learning-kit';
import * as evals from '@future-agi/agent-learning-kit/evals';
import * as local from '@future-agi/agent-learning-kit/local';
import * as evalsLocal from '@future-agi/agent-learning-kit/evals/local';

const runtimeExports = {
  Comparator, EvalInfoResponseHandler, EvalResponseHandler, EvalTags, EvalTemplateManager,
  Evaluator, Execution, ExecutionError, ExecutionMode, JsonResultHandler, OutputType,
  Protect, RequiredKeys, Templates, evaluate, evaluate_pipeline, get_eval_result,
  get_pipeline_results, list_evaluations, normalizeStatus, protect,
};
assert.equal(Object.keys(runtimeExports).length, 21);
assert.deepEqual(Object.keys(root).sort(), Object.keys(runtimeExports).sort());
assert.deepEqual(Object.keys(evals).sort(), Object.keys(root).sort());
for (const [name, value] of Object.entries(runtimeExports)) {
  assert.notEqual(value, undefined, `root.${name}`);
  assert.equal(evals[name], value, `/evals aliases root.${name}`);
}
assert.equal(typeof evaluate, 'function');
assert.equal(evaluate.length, 2, 'two required parameters followed by defaulted options');
for (const name of ['evaluate_pipeline', 'get_eval_result', 'get_pipeline_results', 'list_evaluations', 'protect']) {
  assert.equal(typeof root[name], 'function', name);
}

for (const name of [
  'LocalEvaluator', 'HybridEvaluator', 'OllamaLLM', 'AutoEvalPipeline', 'autoEvaluate',
  'Scanner', 'quickScan', 'securityScan', 'StreamingEvaluator', 'evaluateStream',
]) {
  assert.equal(typeof local[name], 'function', `/local.${name}`);
}
assert.deepEqual(Object.keys(evalsLocal).sort(), Object.keys(local).sort());
for (const [name, value] of Object.entries(local)) {
  assert.equal(evalsLocal[name], value, `/evals/local aliases /local.${name}`);
}

// No cloud or provider calls. These local metric controls need no credentials.
const evaluator = new local.LocalEvaluator();
const positive = evaluator.evaluate('contains', [{ response: 'Hello world' }], { keyword: 'world' });
const negative = evaluator.evaluate('contains', [{ response: 'Hello world' }], { keyword: 'absent' });
assert.equal(positive.results.eval_results[0].output, 1);
assert.equal(negative.results.eval_results[0].output, 0);

for (const subpath of ['local/autoeval', 'local/scanner', 'local/streaming', 'cli']) {
  await assert.rejects(import(`@future-agi/agent-learning-kit/${subpath}`), {
    code: 'ERR_PACKAGE_PATH_NOT_EXPORTED',
  });
}
console.log('PASS packed ESM consumer: root names, public aliases, local controls, unsupported deep paths');
