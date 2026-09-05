import { readFileSync } from 'node:fs';
import process from 'node:process';

import { format, resolveConfig } from 'prettier';

const targets = [
  'packages/contracts/src/index.ts',
  'packages/contracts/src/index.test.ts',
  'packages/contracts/package.json',
  'package.json',
  'eslint.config.mjs',
  'tsconfig.json'
];

const mismatches = [];

for (const path of targets) {
  const source = readFileSync(path, 'utf8');
  const options = await resolveConfig(path);
  const canonical = await format(source, { ...options, filepath: path });
  if (source !== canonical) {
    mismatches.push({ path, canonical });
  }
}

if (mismatches.length > 0) {
  for (const mismatch of mismatches) {
    process.stderr.write(`FORMAT_MISMATCH=${mismatch.path}\n`);
    process.stderr.write(`CANONICAL_CONTENT_START\n${mismatch.canonical}CANONICAL_CONTENT_END\n`);
  }
  process.exit(1);
}
