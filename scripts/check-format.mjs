import { spawnSync } from 'node:child_process';

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
  process.exit(check.status ?? 1);
}
