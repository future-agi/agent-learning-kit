'use strict';

// Invoked by pack:candidate after a successful clean build. Never pack the source tree.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { checkArtifacts, isAllowedPath } = require('./check-artifacts.cjs');

let temporary;
try {
  assert.equal(process.argv.length, 3, 'usage: pnpm run pack:candidate <empty-output-directory>');
  const packageRoot = path.resolve(__dirname, '../..');
  const output = path.resolve(process.argv[2]);
  const relative = path.relative(packageRoot, output);
  assert(relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative), 'candidate output must be outside the source package');
  fs.mkdirSync(output, { recursive: true });
  assert.equal(fs.readdirSync(output).length, 0, 'candidate output directory must be empty');
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'alk-candidate-'));
  const stage = path.join(temporary, 'stage');
  const home = path.join(temporary, 'home');
  fs.mkdirSync(stage);
  fs.mkdirSync(home);
  const copyFile = (relativeFile) => {
    assert(isAllowedPath(relativeFile), `unexpected artifact path: ${relativeFile}`);
    const source = path.join(packageRoot, relativeFile);
    assert(fs.lstatSync(source).isFile() && !fs.lstatSync(source).isSymbolicLink(), `not a regular file: ${relativeFile}`);
    const destination = path.join(stage, relativeFile);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
  };
  const visit = (relativeDirectory) => {
    assert(isAllowedPath(relativeDirectory, true), `unexpected build directory: ${relativeDirectory}`);
    const directory = path.join(packageRoot, relativeDirectory);
    assert(fs.lstatSync(directory).isDirectory() && !fs.lstatSync(directory).isSymbolicLink(), 'no source directory links');
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const relativeFile = path.posix.join(relativeDirectory, entry.name);
      assert(!entry.isSymbolicLink(), `no build links: ${relativeFile}`);
      if (entry.isDirectory()) visit(relativeFile);
      else {
        assert(entry.isFile() && /\.(?:js|d\.ts)$/.test(entry.name), `unexpected build output: ${relativeFile}`);
        copyFile(relativeFile);
      }
    }
  };
  visit('dist/esm');
  for (const file of ['LICENSE', 'NOTICE', 'README.md']) copyFile(file);
  const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
  delete manifest.scripts;
  delete manifest.devDependencies;
  delete manifest.packageManager;
  delete manifest.engines.pnpm;
  fs.writeFileSync(path.join(stage, 'package.json'), JSON.stringify(manifest, null, 2) + '\n');
  checkArtifacts(stage);

  const userConfig = path.join(temporary, 'user.npmrc');
  const globalConfig = path.join(temporary, 'global.npmrc');
  fs.writeFileSync(userConfig, '');
  fs.writeFileSync(globalConfig, '');
  const result = JSON.parse(execFileSync('npm', [
    'pack', '--offline', '--ignore-scripts', '--json', '--pack-destination', output, stage,
  ], {
    cwd: stage,
    env: {
      PATH: process.env.PATH,
      HOME: home,
      TMPDIR: temporary,
      NPM_CONFIG_USERCONFIG: userConfig,
      NPM_CONFIG_GLOBALCONFIG: globalConfig,
      NPM_CONFIG_CACHE: path.join(temporary, 'npm-cache'),
      NPM_CONFIG_OFFLINE: 'true',
      NPM_CONFIG_AUDIT: 'false',
      NPM_CONFIG_FUND: 'false',
      NPM_CONFIG_UPDATE_NOTIFIER: 'false',
    },
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }));
  assert(Array.isArray(result) && result.length === 1, 'expected one packed candidate');
  assert(typeof result[0].filename === 'string' && path.basename(result[0].filename) === result[0].filename, 'unsafe packed filename');
  const tarball = path.join(output, result[0].filename);
  const audit = checkArtifacts(tarball);
  console.log(JSON.stringify({ status: 'CANDIDATE_PACKED_NOT_CONSUMER_PROVEN', tarball, ...audit }, null, 2));
} catch (error) {
  console.error(`FAIL candidate packaging: ${error.message}`);
  process.exitCode = 1;
} finally {
  if (temporary) fs.rmSync(temporary, { recursive: true, force: true });
}
