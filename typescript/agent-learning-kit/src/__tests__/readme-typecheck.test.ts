import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Static only: tsc type-checks the README examples; nothing is executed.
// Each fenced TypeScript block is its own module, so every block must carry
// its own imports exactly as a reader would copy it.
const packageRoot = path.resolve(__dirname, '../..');
const readmePath = path.join(packageRoot, 'README.md');
const tscPath = path.join(packageRoot, 'node_modules/typescript/bin/tsc');

// The public specifiers resolve to the sources that build each export target.
const ENTRY_POINTS: Record<string, string> = {
  '@future-agi/agent-learning-kit': 'src/index.ts',
  '@future-agi/agent-learning-kit/evals': 'src/index.ts',
  '@future-agi/agent-learning-kit/evals/local': 'src/local/index.ts',
  '@future-agi/agent-learning-kit/local': 'src/local/index.ts',
};

interface Snippet {
  fenceLine: number;
  code: string;
}

function typescriptSnippets(markdown: string): Snippet[] {
  const snippets: Snippet[] = [];
  let fence: { line: number; typescript: boolean; body: string[] } | undefined;
  markdown.split('\n').forEach((text, index) => {
    const opener = /^```\s*([A-Za-z0-9_-]*)\s*$/.exec(text);
    if (fence === undefined) {
      if (opener) fence = { line: index + 1, typescript: /^(?:typescript|ts)$/.test(opener[1]), body: [] };
      return;
    }
    if (/^```\s*$/.test(text)) {
      if (fence.typescript) snippets.push({ fenceLine: fence.line, code: `${fence.body.join('\n')}\n` });
      fence = undefined;
      return;
    }
    fence.body.push(text);
  });
  expect(fence).toBeUndefined();
  return snippets;
}

function commonDirectory(a: string, b: string): string {
  const left = a.split(path.sep);
  const right = b.split(path.sep);
  let shared = 0;
  while (shared < left.length && shared < right.length && left[shared] === right[shared]) shared += 1;
  return left.slice(0, shared).join(path.sep) || path.sep;
}

describe('README TypeScript examples', () => {
  test('every fenced TypeScript block type-checks under the package tsconfig', () => {
    const markdown = fs.readFileSync(readmePath, 'utf8');
    const snippets = typescriptSnippets(markdown);
    // Vacuity guard: every TypeScript fence is extracted, and there is at least one.
    expect(snippets.length).toBeGreaterThan(0);
    expect(snippets).toHaveLength((markdown.match(/^```(?:typescript|ts)\s*$/gm) ?? []).length);
    expect(fs.existsSync(tscPath)).toBe(true);

    const workdir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'alk-readme-')));
    try {
      const blockLine = new Map<string, number>();
      const files = snippets.map(snippet => {
        const name = `readme-block-${snippet.fenceLine}.ts`;
        blockLine.set(name, snippet.fenceLine);
        const file = path.join(workdir, name);
        fs.writeFileSync(file, snippet.code);
        return file;
      });
      // Inherit the package compiler options; override only what places a
      // throwaway program outside src without writing into the package.
      const config = {
        extends: path.join(packageRoot, 'tsconfig.json'),
        compilerOptions: {
          noEmit: true,
          composite: false,
          rootDir: commonDirectory(workdir, packageRoot),
          tsBuildInfoFile: path.join(workdir, 'readme.tsbuildinfo'),
          typeRoots: [path.join(packageRoot, 'node_modules/@types')],
          paths: Object.fromEntries(
            Object.entries(ENTRY_POINTS).map(([specifier, source]) => [specifier, [path.join(packageRoot, source)]]),
          ),
        },
        files,
        include: [],
      };
      const project = path.join(workdir, 'tsconfig.json');
      fs.writeFileSync(project, JSON.stringify(config, null, 2));

      const run = spawnSync(process.execPath, [tscPath, '--noEmit', '-p', project, '--pretty', 'false'], {
        cwd: workdir,
        encoding: 'utf8',
      });
      expect(run.error).toBeUndefined();
      // Report positions as README lines; never print local temp or checkout paths.
      const diagnostics = `${run.stdout ?? ''}${run.stderr ?? ''}`
        .split('\n')
        .map(line => line.trimEnd())
        .filter(line => line.length > 0)
        .map(line => line
          .replace(/^(?:.*[\\/])?(readme-block-\d+\.ts)\((\d+),(\d+)\)/, (_match, name: string, row: string, column: string) =>
            `README.md:${blockLine.get(name)! + Number(row)}:${column}`)
          .split(`${packageRoot}${path.sep}`).join('')
          .split(`${workdir}${path.sep}`).join(''));
      expect({ status: run.status, diagnostics }).toEqual({ status: 0, diagnostics: [] });
    } finally {
      fs.rmSync(workdir, { recursive: true, force: true });
    }
  }, 300_000);
});
