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
    await new Promise(resolve => setTimeout(resolve, 5));
    console.log('NODE_AOT_WORKLOAD_OK');
  } finally {
    fs.rmSync(directory, {recursive: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
