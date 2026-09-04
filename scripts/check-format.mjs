import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { format, resolveConfig } from 'prettier';

const targets = [
  'packages/**/*.{ts,tsx,js,mjs,json}',
  'package.json',
  'eslint.config.mjs',
  'tsconfig.json'
];

const check = spawnSync('pnpm', ['exec', 'prettier', '--check', ...targets], {
  stdio: 'inherit'
});

if (check.status !== 0) {
  const path = 'packages/contracts/src/index.ts';
  const source = readFileSync(path, 'utf8');
  const options = await resolveConfig(path);
  const canonical = await format(source, { ...options, filepath: path });
  const sourceLines = source.split('\n');
  const canonicalLines = canonical.split('\n');
  const changes = canonicalLines
    .map((line, index) => ({ line: index + 1, current: sourceLines[index], canonical: line }))
    .filter(change => change.current !== change.canonical);

  process.stderr.write(`FORMAT_DIFF=${JSON.stringify(changes)}\n`);
  process.exit(check.status ?? 1);
}
