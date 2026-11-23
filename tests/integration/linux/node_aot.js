// SPDX-License-Identifier: GPL-2.0-or-later
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');

async function main() {
  assert.equal(process.version, 'v22.16.0');
  assert(process.execArgv.includes('--jitless'));
  const values = Array.from({length: 10000}, (_, i) => ({i, text: `value-${i}`}));
  const text = JSON.stringify(values);
  assert.deepEqual(JSON.parse(text), values);
  assert.equal(values.reduce((sum, value) => sum + value.i, 0), 49995000);
  const bytes = Buffer.from('Kernarium: Linux Node AOT\nПривет\n');
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),
               crypto.createHash('sha256').update(Buffer.from(bytes.toString())).digest('hex'));
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'node-aot-'));
  try {
    fs.writeFileSync(path.join(directory, 'data.json'), text);
    assert.deepEqual(JSON.parse(await fs.promises.readFile(path.join(directory, 'data.json'))), values);
    const child = spawnSync(process.execPath, ['--jitless', '--version'], {encoding: 'utf8', timeout: 30000});
    assert.equal(child.status, 0);
    assert.equal(child.stdout.trim(), process.version);
    const identity = JSON.parse(fs.readFileSync('/opt/kernarium-node/identity.json', 'utf8'));
    const npm = spawnSync('npm', ['--version'], {encoding: 'utf8', timeout: 30000});
    assert.equal(npm.status, 0, `npm --version: signal=${npm.signal}\n${npm.stderr}`);
    assert.equal(npm.stdout.trim(), identity.npm_version);
    const npx = spawnSync('npx', ['--version'], {encoding: 'utf8', timeout: 30000});
    assert.equal(npx.status, 0, `npx --version: signal=${npx.signal}\n${npx.stderr}`);
    assert.equal(npx.stdout.trim(), identity.npm_version);
    const packageDirectory = path.join(directory, 'package');
    const project = path.join(directory, 'project');
    fs.mkdirSync(packageDirectory);
    fs.mkdirSync(project);
    fs.writeFileSync(path.join(packageDirectory, 'package.json'),
                     JSON.stringify({name: 'kernarium-npm-probe', version: '1.0.0', main: 'index.js'}));
    fs.writeFileSync(path.join(packageDirectory, 'index.js'), 'module.exports = 42;\n');
    fs.writeFileSync(path.join(project, 'package.json'), JSON.stringify({name: 'probe', private: true}));
    const installed = spawnSync('npm', ['install', '--offline', '--ignore-scripts', '--no-audit',
                               '--no-fund', '--prefix', project, packageDirectory],
                               {encoding: 'utf8', timeout: 60000});
    assert.equal(installed.status, 0, `npm install: signal=${installed.signal}\n${installed.stderr}`);
    assert.equal(require(path.join(project, 'node_modules/kernarium-npm-probe')), 42);
    await new Promise(resolve => setTimeout(resolve, 5));
    console.log('NODE_AOT_WORKLOAD_OK');
  } finally {
    fs.rmSync(directory, {recursive: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
