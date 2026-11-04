'use strict';

// Audit a stage directory or an npm-generated .tgz without extracting untrusted paths.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const LIMIT = 64 * 1024 * 1024;
const ROOT_FILES = new Set(['package.json', 'LICENSE', 'NOTICE', 'README.md']);

const FORBIDDEN_SEGMENTS = new Set([
  'fixtures', '__fixtures__', 'scripts', '__tests__', 'test', 'tests', '__mocks__', 'examples', 'node_modules',
]);

// Pure, shared policy for canonical package-relative POSIX paths, including empty directories.
function isAllowedPath(name, directory = false) {
  // The ASCII allowlist below permits capitals; forbidden spellings must not depend on case.
  if (name.split('/').some(segment => FORBIDDEN_SEGMENTS.has(segment.toLowerCase())) || /\.(?:test|spec)\./i.test(name)) return false;
  if (directory) {
    return name === '' || name === 'dist' || name === 'dist/esm' ||
      /^dist\/esm\/(?:[\w-]+\/)*[\w-]+$/.test(name);
  }
  return ROOT_FILES.has(name) || /^dist\/esm\/(?:[\w-]+\/)*[\w-]+\.(?:js|d\.ts)$/.test(name);
}

function readDirectory(directory) {
  const files = new Map();
  function visit(relative) {
    for (const name of fs.readdirSync(path.join(directory, relative))) {
      const child = path.posix.join(relative, name);
      const absolute = path.join(directory, child);
      const stat = fs.lstatSync(absolute);
      assert(!stat.isSymbolicLink(), `symlink: ${child}`);
      if (stat.isDirectory()) {
        assert(isAllowedPath(child, true), `unexpected directory: ${child}`);
        visit(child);
      } else {
        assert(stat.isFile() && isAllowedPath(child), `unexpected artifact: ${child}`);
        files.set(child, fs.readFileSync(absolute));
      }
    }
  }
  visit('');
  return files;
}

function readTarball(filename) {
  const packed = fs.readFileSync(filename);
  assert(packed.length <= LIMIT, 'compressed archive too large');
  const tar = zlib.gunzipSync(packed, { maxOutputLength: LIMIT });
  const files = new Map();
  let offset = 0;
  let ended = false;
  const text = buffer => buffer.toString('utf8').replace(/\0.*$/s, '');
  const octal = buffer => {
    const value = text(buffer).trim();
    assert(/^[0-7]+$/.test(value), 'invalid tar numeric field');
    return parseInt(value, 8);
  };
  while (offset + 512 <= tar.length) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every(byte => byte === 0)) {
      assert(tar.length - offset >= 1024, 'missing tar end blocks');
      assert(tar.subarray(offset).every(byte => byte === 0), 'data after tar terminator');
      ended = true;
      break;
    }
    const checksum = header.reduce((sum, byte, index) => sum + (index >= 148 && index < 156 ? 32 : byte), 0);
    assert.equal(checksum, octal(header.subarray(148, 156)), 'tar header checksum');
    const prefix = text(header.subarray(345, 500));
    const name = (prefix ? `${prefix}/` : '') + text(header.subarray(0, 100));
    const kind = header[156];
    const size = octal(header.subarray(124, 136));
    assert(name.startsWith('package/'), `not an npm package path: ${name}`);
    const relative = name.slice('package/'.length).replace(/\/$/, '');
    assert(!relative.includes('\\') && !relative.split('/').some(part => part === '.' || part === '..'), 'unsafe archive path');
    // No links, devices, GNU extensions or PAX overrides. Current package paths fit USTAR.
    assert(kind === 0 || kind === 48 || kind === 53, `unsupported tar entry type: ${name}`);
    assert.equal(text(header.subarray(157, 257)), '', 'link target is forbidden');
    offset += 512;
    assert(offset + size <= tar.length, 'truncated tar content');
    if (kind === 53) {
      assert.equal(size, 0, 'nonempty directory entry');
      assert(isAllowedPath(relative, true), `unexpected directory: ${relative}`);
    } else {
      assert(isAllowedPath(relative), `unexpected packed file: ${relative}`);
      assert(!files.has(relative), `duplicate packed file: ${relative}`);
      files.set(relative, tar.subarray(offset, offset + size));
    }
    offset += Math.ceil(size / 512) * 512;
  }
  assert(ended, 'missing tar terminator');
  return { files, sha256: crypto.createHash('sha256').update(packed).digest('hex') };
}

function checkFiles(files) {
  for (const name of ROOT_FILES) assert(files.has(name), `missing ${name}`);
  const manifest = JSON.parse(files.get('package.json').toString('utf8'));
  assert.equal(manifest.name, '@future-agi/agent-learning-kit');
  assert.equal(manifest.version, '0.4.0');
  assert.equal(manifest.type, 'module');
  assert(!Object.hasOwn(manifest, 'scripts'), 'candidate must have NO scripts field');
  assert(!Object.hasOwn(manifest, 'devDependencies'), 'candidate must have no devDependencies');
  assert.equal(manifest.sideEffects, true);
  assert.equal(manifest.engines.node, '>=18.0.0');
  assert.deepEqual(Object.keys(manifest.exports).sort(), ['.', './evals', './evals/local', './local']);
  for (const [entry, conditions] of Object.entries(manifest.exports)) {
    assert.deepEqual(Object.keys(conditions), ['types', 'import'], `${entry}: types first, ESM only`);
    const stem = entry.endsWith('/local') ? 'local/index' : 'index';
    assert.equal(conditions.types, `./dist/esm/${stem}.d.ts`);
    assert.equal(conditions.import, `./dist/esm/${stem}.js`);
    for (const target of Object.values(conditions)) assert(files.has(target.slice(2)), `missing export ${target}`);
  }
  assert.equal(manifest.main, './dist/esm/index.js');
  assert.equal(manifest.module, './dist/esm/index.js');
  assert.equal(manifest.types, './dist/esm/index.d.ts');
  for (const name of files.keys()) {
    if (!name.startsWith('dist/')) continue;
    const partner = name.endsWith('.d.ts') ? name.slice(0, -5) + '.js' : name.slice(0, -3) + '.d.ts';
    assert(files.has(partner), `missing emitted partner: ${partner}`);
  }
  return { name: manifest.name, version: manifest.version, files: files.size };
}

function checkArtifacts(input) {
  if (fs.lstatSync(input).isDirectory()) return checkFiles(readDirectory(input));
  const { files, sha256 } = readTarball(input);
  return { ...checkFiles(files), sha256 };
}
module.exports = { checkArtifacts, isAllowedPath };

if (require.main === module) {
  try {
    assert.equal(process.argv.length, 3, 'usage: node check-artifacts.cjs <stage-directory|tarball>');
    console.log(JSON.stringify({ status: 'PASS_ARTIFACT_LAYOUT', ...checkArtifacts(path.resolve(process.argv[2])) }));
  } catch (error) {
    console.error(`FAIL artifact audit: ${error.message}`);
    process.exitCode = 1;
  }
}
