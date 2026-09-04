import { spawnSync } from 'node:child_process';

const result = spawnSync('uv', ['run', 'mypy'], { encoding: 'utf8' });

process.stdout.write(result.stdout);
process.stderr.write(result.stderr);

if (result.status !== 0) {
  const output = `${result.stdout}${result.stderr}`;
  for (const line of output.split('\n')) {
    const match = line.match(/^(?<path>[^:]+):(?<line>\d+): error: (?<message>.+?)(?:\s+\[[^\]]+\])?$/);
    if (match?.groups) {
      const { path, line: lineNumber, message } = match.groups;
      console.log(`::error file=${path},line=${lineNumber},title=MyPy::${message}`);
    }
  }
}

process.exit(result.status ?? 1);
