import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

// Static inspection only: every declared runtime dependency must be imported
// by shipped source. Tests do not count, because they are not published.
const packageRoot = path.resolve(__dirname, '../..');
const sourceRoot = path.join(packageRoot, 'src');
const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));

function productionSources(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === '__tests__') return [];
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) return productionSources(file);
    return entry.name.endsWith('.ts') && !/\.(?:test|spec)\.ts$/.test(entry.name) ? [file] : [];
  });
}

function packageName(specifier: string): string | undefined {
  if (specifier.startsWith('.') || specifier.startsWith('/') || specifier.startsWith('node:')) return undefined;
  const segments = specifier.split('/');
  return specifier.startsWith('@') ? segments.slice(0, 2).join('/') : segments[0];
}

function importedPackages(): Set<string> {
  const imported = new Set<string>();
  const record = (specifier: ts.Node | undefined) => {
    if (!specifier || !ts.isStringLiteralLike(specifier)) return;
    const name = packageName(specifier.text);
    if (name) imported.add(name);
  };
  for (const file of productionSources(sourceRoot)) {
    const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
    const visit = (node: ts.Node) => {
      if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
        record(node.moduleSpecifier);
      } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
        record(node.argument.literal);
      } else if (ts.isCallExpression(node) && (
        node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) && node.expression.text === 'require')
      )) {
        record(node.arguments[0]);
      } else if (ts.isExternalModuleReference(node)) {
        record(node.expression);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return imported;
}

describe('runtime dependency set', () => {
  test('every declared runtime dependency is imported by shipped source', () => {
    const imported = importedPackages();
    // Vacuity guards: the scan sees the static transport import and the
    // dynamically imported tracing helper.
    expect(imported.has('axios')).toBe(true);
    expect(imported.has('@traceai/fi-core')).toBe(true);
    const declared = Object.keys(manifest.dependencies ?? {}).sort();
    expect(declared.length).toBeGreaterThan(0);
    expect(declared.filter(name => !imported.has(name))).toEqual([]);
  });
});
