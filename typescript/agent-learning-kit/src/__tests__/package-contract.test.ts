import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

// Static inspection only: never load the SDK, a provider, or a backend transport.
const packageRoot = path.resolve(__dirname, '../..');
const sourceRoot = path.join(packageRoot, 'src');
const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));

const ROOT_NAMES = [
  'AggregationFunction', 'BatchRunResult', 'BulkDeleteResponse', 'Comparator',
  'CompositeChildAxis', 'CompositeChildResult', 'CompositeChildSummary',
  'CompositeCreateResponse', 'CompositeDetailResponse', 'CompositeExecutionResponse',
  'ConfigParam', 'ConfigPossibleValues', 'CreateCompositeOptions', 'CreateTemplateOptions',
  'DatapointFieldAnnotation', 'EvalInfoResponseHandler', 'EvalResponseHandler', 'EvalResult',
  'EvalResultMetric', 'EvalTags', 'EvalTemplate', 'EvalTemplateManager', 'EvalTemplateOutputType',
  'EvalType', 'EvaluateOptions', 'Evaluator', 'EvaluatorConfig', 'ExecuteCompositeOptions',
  'Execution', 'ExecutionError', 'ExecutionKind', 'ExecutionMode', 'ExecutionStatus',
  'GroundTruthConfigData', 'GroundTruthConfigResponse', 'GroundTruthDataResponse',
  'GroundTruthDeleteResponse', 'GroundTruthItemResponse', 'GroundTruthListResponse',
  'GroundTruthRoleMappingResponse', 'GroundTruthSearchResponse', 'GroundTruthStatusResponse',
  'GroundTruthUploadResponse', 'GroundTruthVariableMappingResponse', 'JsonResultHandler',
  'JudgeResult', 'ListTemplatesOptions', 'LocalEvaluationResult', 'LocalLLMConfig',
  'OutputType', 'PipelineEvalData', 'PipelineResult', 'PlaygroundRunResponse', 'Protect',
  'RequiredKeys', 'TemplateChartsResponse', 'TemplateCreateResponse', 'TemplateDetailResponse',
  'TemplateDuplicateResponse', 'TemplateFeedbackListResponse', 'TemplateListItem',
  'TemplateListResponse', 'TemplateUpdateResponse', 'TemplateUsageResponse', 'Templates',
  'VersionCreateResponse', 'VersionItem', 'VersionListResponse', 'VersionRestoreResponse',
  'VersionSetDefaultResponse', 'WaitOptions', 'evaluate', 'evaluate_pipeline', 'get_eval_result',
  'get_pipeline_results', 'list_evaluations', 'normalizeStatus', 'protect',
].sort();

const RUNTIME_NAMES = [
  'Comparator', 'EvalInfoResponseHandler', 'EvalResponseHandler', 'EvalTags',
  'EvalTemplateManager', 'Evaluator', 'Execution', 'ExecutionError', 'ExecutionMode',
  'JsonResultHandler', 'OutputType', 'Protect', 'RequiredKeys', 'Templates', 'evaluate',
  'evaluate_pipeline', 'get_eval_result', 'get_pipeline_results', 'list_evaluations',
  'normalizeStatus', 'protect',
].sort();

function parse(file: string): ts.SourceFile {
  return ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
}

function productionSources(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === '__tests__') return [];
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) return productionSources(file);
    return entry.name.endsWith('.ts') && !/\.(?:test|spec)\.ts$/.test(entry.name) ? [file] : [];
  });
}

function exportTargets(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  if (value && typeof value === 'object') return Object.values(value).flatMap(exportTargets);
  return [];
}

function exportConditions(value: unknown): string[] {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, child]) => [key, ...exportConditions(child)]);
}

function interfaceOptions(name: string): string[] {
  const source = parse(path.join(sourceRoot, 'types.ts'));
  const declaration = source.statements.find(
    (node): node is ts.InterfaceDeclaration => ts.isInterfaceDeclaration(node) && node.name.text === name,
  );
  expect(declaration).toBeDefined();
  return declaration!.members.map(member => member.name!.getText(source)).sort();
}

function compact(node: ts.Node | undefined, source: ts.SourceFile): string | undefined {
  return node?.getText(source).replace(/\s+/g, '');
}

describe('installed ESM package contract (static RED controls)', () => {
  test('version is the approved 0.4.0', () => {
    expect(manifest.version).toBe('0.4.0');
  });

  test('exports never advertises a require condition', () => {
    expect(exportConditions(manifest.exports)).not.toContain('require');
  });

  test('every exported target is in the one ESM build', () => {
    const targets = exportTargets(manifest.exports);
    expect(targets.length).toBeGreaterThan(0);
    expect(targets.filter(target => !target.startsWith('./dist/esm/'))).toEqual([]);
  });

  test('types precede import for each supported entry point', () => {
    expect(manifest.exports).toEqual({
      '.': { types: './dist/esm/index.d.ts', import: './dist/esm/index.js' },
      './evals': { types: './dist/esm/index.d.ts', import: './dist/esm/index.js' },
      './evals/local': { types: './dist/esm/local/index.d.ts', import: './dist/esm/local/index.js' },
      './local': { types: './dist/esm/local/index.d.ts', import: './dist/esm/local/index.js' },
    });
    for (const conditions of Object.values(manifest.exports)) {
      expect(Object.keys(conditions as object)).toEqual(['types', 'import']);
    }
    expect(manifest.type).toBe('module');
    expect(manifest.main).toBe('./dist/esm/index.js');
    expect(manifest.module).toBe('./dist/esm/index.js');
    expect(manifest.types).toBe('./dist/esm/index.d.ts');
    expect(manifest.sideEffects).toBe(true); // Conservative: no tree-shaking safety claim.
    expect(manifest.engines.node).toBe('>=18.0.0');
  });

  test('ships only ESM JavaScript, declarations and public license/readme files', () => {
    expect(manifest.files).toEqual([
      'dist/esm/**/*.js', 'dist/esm/**/*.d.ts', 'LICENSE', 'NOTICE', 'README.md',
    ]);
    for (const lifecycle of ['prepare', 'preinstall', 'install', 'postinstall']) {
      expect(manifest.scripts[lifecycle]).toBeUndefined();
    }
    expect(manifest.scripts.build).toBe('rimraf dist && tsc --build tsconfig.json');
    expect(manifest.scripts['pack:candidate']).toBe(
      'pnpm run build && node scripts/consumer-smoke/stage-package.cjs',
    );
    expect(manifest.devDependencies['tsc-alias']).toBeUndefined();
    expect(manifest.devDependencies.typescript).toBe('5.9.3');
  });

  test('one build emits declarations beside ESM and excludes test sources', () => {
    const config = JSON.parse(fs.readFileSync(path.join(packageRoot, 'tsconfig.json'), 'utf8'));
    expect(config.compilerOptions).toMatchObject({
      module: 'ESNext', moduleResolution: 'bundler', outDir: 'dist/esm', rootDir: 'src',
      declaration: true, declarationMap: false, sourceMap: false, noEmitOnError: true,
    });
    expect(config.compilerOptions.paths).toBeUndefined();
    expect(config.compilerOptions.baseUrl).toBeUndefined();
    expect(config.exclude).toEqual(expect.arrayContaining(['src/**/__tests__/**', 'src/**/*.test.ts', 'src/**/*.spec.ts']));
    expect(fs.existsSync(path.join(packageRoot, 'tsconfig.esm.json'))).toBe(false);
    expect(fs.existsSync(path.join(packageRoot, 'tsconfig.esnext.json'))).toBe(false);
  });

  test('all relative source module edges use resolvable .js paths, including directory indexes', () => {
    const violations: string[] = [];
    let relativeEdges = 0;
    for (const file of productionSources(sourceRoot)) {
      const source = parse(file);
      const check = (specifier: ts.Expression | ts.TypeNode) => {
        if (!ts.isStringLiteralLike(specifier)) return;
        const text = specifier.text;
        if (!text.startsWith('./') && !text.startsWith('../')) return;
        relativeEdges += 1;
        const target = path.resolve(path.dirname(file), text.replace(/\.js$/, '.ts'));
        if (!text.endsWith('.js') || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
          violations.push(`${path.relative(sourceRoot, file)}: ${text}`);
        }
      };
      const visit = (node: ts.Node) => {
        if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
          if (node.moduleSpecifier) check(node.moduleSpecifier);
        } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
          check(node.argument.literal);
        } else if (ts.isCallExpression(node) && (
          node.expression.kind === ts.SyntaxKind.ImportKeyword ||
          (ts.isIdentifier(node.expression) && node.expression.text === 'require')
        )) {
          if (node.arguments[0]) check(node.arguments[0]);
        } else if (ts.isExternalModuleReference(node) && node.expression) {
          check(node.expression);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(relativeEdges).toBeGreaterThan(0);
    expect(violations).toEqual([]);
  });
});

describe('candidate security boundaries (static RED on baseline and round 1)', () => {
  test('PKG-SEC-01: npm uses only a private copy of the caller cache', () => {
    const runner = path.join(packageRoot, 'scripts/consumer-smoke/run.sh');
    // Missing helpers on fb3db468 must fail by assertion, not an ENOENT exception.
    expect(fs.existsSync(runner)).toBe(true);
    const script = fs.readFileSync(runner, 'utf8');
    expect(script).toContain('supplied_cache=$(CDPATH= cd -- "$2" && pwd -P)');
    expect(script).toContain('temporary=$(mktemp -d ');
    const copy = 'cp -R -P -- "$supplied_cache" "$temporary/npm-cache"';
    expect(script.split('\n').filter(line => /\$(?:supplied_cache\b|\{supplied_cache\})/.test(line))).toEqual([copy]);
    const cacheArguments = [...script.matchAll(/(?:\bNPM_CONFIG_CACHE=|--cache(?:=|\s+))("[^"]*"|'[^']*'|[^\s\\]+)/g)]
      .map(match => match[1]);
    expect(cacheArguments).toEqual(['"$temporary/npm-cache"', '"$temporary/npm-cache"']);
    // Preserved nested symlinks must not redirect npm writes outside the private copy.
    const linkCheck = 'cache_links=$(find -P "$temporary/npm-cache" -type l -print -quit)';
    expect(script).toContain(linkCheck);
    expect(script).toContain('[[ -z "$cache_links" ]] || { printf');
    expect(script).toContain("'FAIL npm cache must not contain symlinks' >&2; exit 2; }");
    expect(script.indexOf(linkCheck)).toBeGreaterThan(script.indexOf(copy));
    // PKG-COR-R2-01: BSD chmod treats -- after the mode as an unrelated file operand.
    const makeWritable = 'chmod -R u+rwX "$temporary/npm-cache"';
    expect(script).toContain(makeWritable);
    expect(script).not.toContain('chmod -R u+rwX --');
    expect(script.indexOf(makeWritable)).toBeGreaterThan(script.indexOf(linkCheck));
    expect(script.indexOf('clean "$npm_bin" install')).toBeGreaterThan(script.indexOf(makeWritable));
  });

  test('PKG-SEC-02: shared pure path policy rejects fixtures, tooling and tests at every depth', () => {
    const checker = path.join(packageRoot, 'scripts/consumer-smoke/check-artifacts.cjs');
    expect(fs.existsSync(checker)).toBe(true);
    // Loading the guarded CJS helper defines the pure policy; it does not read archives or run tar.
    const policy = require(checker) as { isAllowedPath?: (name: string, directory?: boolean) => boolean };
    expect(typeof policy.isAllowedPath).toBe('function');
    const cases: Array<[string, boolean, boolean]> = [
      ['package.json', false, true], ['LICENSE', false, true], ['NOTICE', false, true],
      ['README.md', false, true], ['dist/esm/index.js', false, true],
      ['dist/esm/index.d.ts', false, true], ['dist/esm/local/metrics/code/index.js', false, true],
      ['dist/esm/local/metrics/code/index.d.ts', false, true],
      ['dist/esm/local/metrics/code/secrets-detection.js', false, true],
      ['dist/esm/fixtures-helper.js', false, true],
      ['dist/esm/testing-utils.js', false, true], ['dist/esm/latest.js', false, true],
      ['dist/esm/contest.d.ts', false, true], ['dist/esm/Testing-utils.js', false, true],
      ['dist/esm/Local/Index.js', false, true], ['dist/esm/Local', true, true],
      ['dist/esm/a.Test.js', false, false], ['dist/esm/b.SPEC.d.ts', false, false],
      ['', true, true], ['dist', true, true], ['dist/esm', true, true],
      ['dist/esm/local/metrics', true, true],
      ['dist/src/index.js', false, false], ['dist/esm/index.ts', false, false],
      ['dist/esm/index.js.map', false, false], ['dist/esm/local/index.d.ts.map', false, false],
      ['dist/esm/local', false, false], ['dist/esm/local.js', true, false],
      ['/dist/esm/index.js', false, false], ['dist/esm/../index.js', false, false],
      ['dist/esm/./index.js', false, false], ['dist/esm//index.js', false, false],
      ['dist/esm/local\\index.js', false, false], ['src/index.ts', false, false],
      ['dist/esm/../local', true, false], ['dist/esm//local', true, false],
    ];
    for (const segment of ['fixtures', '__fixtures__', 'scripts', '__tests__', 'test', 'tests', '__mocks__', 'examples', 'node_modules']) {
      // Cover lowercase, uppercase and mixed case, including empty directory entries.
      const spellings = [segment, segment.toUpperCase(), segment.replace(/[a-z]/, letter => letter.toUpperCase())];
      for (const spelling of spellings) {
        for (const prefix of ['dist/esm', 'dist/esm/local/metrics']) {
          cases.push([`${prefix}/${spelling}`, true, false], [`${prefix}/${spelling}/nested`, true, false]);
          for (const extension of ['js', 'd.ts']) {
            cases.push([`${prefix}/${spelling}/example.${extension}`, false, false]);
            cases.push([`${prefix}/${spelling}/nested/example.${extension}`, false, false]);
          }
        }
      }
    }
    for (const marker of ['test', 'Test', 'TEST', 'spec', 'Spec', 'SPEC']) {
      for (const prefix of ['dist/esm', 'dist/esm/local/metrics']) {
        for (const extension of ['js', 'd.ts']) {
          cases.push([`${prefix}/example.${marker}.${extension}`, false, false]);
        }
      }
    }
    for (const [name, directory, allowed] of cases) {
      expect({ name, directory, allowed: policy.isAllowedPath!(name, directory) }).toEqual({ name, directory, allowed });
    }
  });

  test('PKG-SEC-03: source pack and publish refuse while candidate packing still builds', () => {
    const refusal = 'node -e "console.error(\'Source-tree packing and publishing are disabled; use pnpm run pack:candidate <empty-output-directory>.\'); process.exit(1)"';
    expect(manifest.scripts.prepack).toBe(refusal);
    expect(manifest.scripts.prepublishOnly).toBe(refusal);
    expect(manifest.private).not.toBe(true);
    expect(manifest.scripts.build).toBe('rimraf dist && tsc --build tsconfig.json');
    expect(manifest.scripts['pack:candidate']).toBe(
      'pnpm run build && node scripts/consumer-smoke/stage-package.cjs',
    );
  });
});

describe('public API regression pins (expected GREEN on the baseline too)', () => {
  test('root exports exactly the frozen 78 names, including 21 runtime and 57 type-only names', () => {
    const entry = path.join(sourceRoot, 'index.ts');
    const program = ts.createProgram([entry], {
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
      target: ts.ScriptTarget.ES2020,
      noEmit: true,
      types: [],
    });
    const checker = program.getTypeChecker();
    const symbol = checker.getSymbolAtLocation(program.getSourceFile(entry)!);
    expect(symbol).toBeDefined();
    const exports = checker.getExportsOfModule(symbol!);
    const runtime = exports.filter(exported => {
      const resolved = exported.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(exported) : exported;
      return Boolean(resolved.flags & ts.SymbolFlags.Value);
    }).map(exported => exported.name).sort();
    expect(ROOT_NAMES).toHaveLength(78);
    expect(exports.map(exported => exported.name).sort()).toEqual(ROOT_NAMES);
    expect(runtime).toEqual(RUNTIME_NAMES);
    expect(exports.filter(exported => !runtime.includes(exported.name))).toHaveLength(57);
  });

  test('evaluate has two required arguments and the defaulted modelName options argument', () => {
    const source = parse(path.join(sourceRoot, 'evaluator.ts'));
    const declaration = source.statements.filter(ts.isVariableStatement)
      .flatMap(statement => [...statement.declarationList.declarations])
      .find(node => node.name.getText(source) === 'evaluate');
    expect(declaration).toBeDefined();
    expect(declaration!.initializer && ts.isArrowFunction(declaration!.initializer)).toBe(true);
    const fn = declaration!.initializer as ts.ArrowFunction;
    expect(fn.parameters.map(parameter => ({
      name: parameter.name.getText(source),
      type: compact(parameter.type, source),
      default: compact(parameter.initializer, source),
      optional: Boolean(parameter.questionToken),
      rest: Boolean(parameter.dotDotDotToken),
    }))).toEqual([
      { name: 'evalTemplates', type: 'string|EvalTemplate|(string|EvalTemplate)[]', default: undefined, optional: false, rest: false },
      { name: 'inputs', type: 'Record<string,string|string[]>', default: undefined, optional: false, rest: false },
      { name: 'options', type: 'EvaluatorConfig&EvaluateOptions', default: '{}', optional: false, rest: false },
    ]);
    expect(compact(fn.type, source)).toBe('Promise<BatchRunResult>');
    expect(interfaceOptions('EvaluateOptions')).toEqual([
      'customEvalName', 'errorLocalizer', 'evalConfig', 'isAsync', 'modelName', 'platform', 'timeout', 'traceEval',
    ]);
    expect(interfaceOptions('EvaluatorConfig')).toEqual([
      'fiApiKey', 'fiBaseUrl', 'fiSecretKey', 'langfuseHost', 'langfusePublicKey', 'langfuseSecretKey',
      'maxQueue', 'maxWorkers', 'timeout',
    ]);
  });
});
