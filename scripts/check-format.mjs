import { readFileSync } from 'node:fs';
import process from 'node:process';

import { format, resolveConfig } from 'prettier';

const targets = [
  'packages/contracts/src/index.ts',
  'packages/contracts/src/edge.ts',
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
    mismatches.push(path);
  }
}

if (mismatches.length > 0) {
  const message = `Prettier requires canonical formatting for: ${mismatches.join(', ')}`;
  process.stderr.write(`::error title=Formatter drift::${message}\n`);
  throw new Error(message);
}
