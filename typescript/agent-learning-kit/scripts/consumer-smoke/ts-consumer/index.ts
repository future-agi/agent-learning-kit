import {
  evaluate, list_evaluations, get_eval_result, evaluate_pipeline, get_pipeline_results, protect,
  type AggregationFunction, type BatchRunResult, type BulkDeleteResponse,
  type CompositeChildAxis, type CompositeChildResult, type CompositeChildSummary,
  type CompositeCreateResponse, type CompositeDetailResponse, type CompositeExecutionResponse,
  type ConfigParam, type ConfigPossibleValues, type CreateCompositeOptions, type CreateTemplateOptions,
  type DatapointFieldAnnotation, type EvalResult, type EvalResultMetric, type EvalTemplate,
  type EvalTemplateOutputType, type EvalType, type EvaluateOptions, type EvaluatorConfig,
  type ExecuteCompositeOptions, type ExecutionKind, type ExecutionStatus,
  type GroundTruthConfigData, type GroundTruthConfigResponse, type GroundTruthDataResponse,
  type GroundTruthDeleteResponse, type GroundTruthItemResponse, type GroundTruthListResponse,
  type GroundTruthRoleMappingResponse, type GroundTruthSearchResponse, type GroundTruthStatusResponse,
  type GroundTruthUploadResponse, type GroundTruthVariableMappingResponse, type JudgeResult,
  type ListTemplatesOptions, type LocalEvaluationResult, type LocalLLMConfig, type PipelineEvalData,
  type PipelineResult, type PlaygroundRunResponse, type TemplateChartsResponse, type TemplateCreateResponse,
  type TemplateDetailResponse, type TemplateDuplicateResponse, type TemplateFeedbackListResponse,
  type TemplateListItem, type TemplateListResponse, type TemplateUpdateResponse, type TemplateUsageResponse,
  type VersionCreateResponse, type VersionItem, type VersionListResponse, type VersionRestoreResponse,
  type VersionSetDefaultResponse, type WaitOptions,
} from '@future-agi/agent-learning-kit';
import { evaluate as evaluateAlias, type BatchRunResult as AliasResult } from '@future-agi/agent-learning-kit/evals';
import { LocalEvaluator, AutoEvalPipeline, Scanner, StreamingEvaluator } from '@future-agi/agent-learning-kit/local';
import { LocalEvaluator as LocalAlias } from '@future-agi/agent-learning-kit/evals/local';

// Pin every one of the 57 type-only root names without executing a backend call.
export type RootTypeContract = [
  AggregationFunction, BatchRunResult, BulkDeleteResponse, CompositeChildAxis, CompositeChildResult,
  CompositeChildSummary, CompositeCreateResponse, CompositeDetailResponse, CompositeExecutionResponse,
  ConfigParam, ConfigPossibleValues, CreateCompositeOptions, CreateTemplateOptions, DatapointFieldAnnotation,
  EvalResult, EvalResultMetric, EvalTemplate, EvalTemplateOutputType, EvalType, EvaluateOptions,
  EvaluatorConfig, ExecuteCompositeOptions, ExecutionKind, ExecutionStatus, GroundTruthConfigData,
  GroundTruthConfigResponse, GroundTruthDataResponse, GroundTruthDeleteResponse, GroundTruthItemResponse,
  GroundTruthListResponse, GroundTruthRoleMappingResponse, GroundTruthSearchResponse, GroundTruthStatusResponse,
  GroundTruthUploadResponse, GroundTruthVariableMappingResponse, JudgeResult, ListTemplatesOptions,
  LocalEvaluationResult, LocalLLMConfig, PipelineEvalData, PipelineResult, PlaygroundRunResponse,
  TemplateChartsResponse, TemplateCreateResponse, TemplateDetailResponse, TemplateDuplicateResponse,
  TemplateFeedbackListResponse, TemplateListItem, TemplateListResponse, TemplateUpdateResponse,
  TemplateUsageResponse, VersionCreateResponse, VersionItem, VersionListResponse, VersionRestoreResponse,
  VersionSetDefaultResponse, WaitOptions,
];

// Compiled with noEmit; this function is deliberately never invoked.
export function checkPublicTypes() {
  const options = { modelName: 'test-model' } satisfies EvaluatorConfig & EvaluateOptions;
  const run: Promise<BatchRunResult> = evaluate(
    'Factual Accuracy', { response: ['answer'], context: ['context'] }, options,
  );
  const alias: Promise<AliasResult> = evaluateAlias('Factual Accuracy', { response: ['answer'] }, options);
  const names: Promise<Record<string, any>[]> = list_evaluations();
  const result: Promise<Record<string, any>> = get_eval_result('id');
  const pipeline: Promise<Record<string, any>> = evaluate_pipeline('project', 'v1', [{ output: 'answer' }]);
  const results: Promise<Record<string, any>> = get_pipeline_results('project', ['v1']);
  const guarded: Promise<Record<string, any>> = protect('input', [{ metric: 'Toxicity' }]);
  const local = new LocalEvaluator().evaluate('contains', [{ response: 'answer' }], { keyword: 'answer' });
  const localAlias: LocalEvaluator = new LocalAlias();
  const constructors = [AutoEvalPipeline, Scanner, StreamingEvaluator];
  // Negative controls ensure the declarations do not degrade to any.
  // @ts-expect-error modelName is a string, not a number
  evaluate('Factual Accuracy', { response: ['answer'] }, { modelName: 1 });
  // @ts-expect-error inputs is required
  evaluate('Factual Accuracy');
  return { run, alias, names, result, pipeline, results, guarded, local, localAlias, constructors };
}
