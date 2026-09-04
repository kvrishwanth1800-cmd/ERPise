import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { format } from 'prettier';

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
  process.stderr.write('\nFormatter violations:\n');
  spawnSync('pnpm', ['exec', 'prettier', '--list-different', ...targets], {
    stdio: 'inherit'
  });

  const path = 'packages/contracts/src/index.ts';
  const source = readFileSync(path, 'utf8');
  const canonical = await format(source, { filepath: path });
  process.stderr.write(`\nCanonical ${path}:\n${canonical}`);
  process.exit(check.status ?? 1);
}
