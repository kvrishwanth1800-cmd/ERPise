import { spawnSync } from 'node:child_process';
import { exit, stderr, stdout } from 'node:process';

const result = spawnSync('uv', ['run', 'mypy'], { encoding: 'utf8' });

stdout.write(result.stdout);
stderr.write(result.stderr);

if (result.status !== 0) {
  const output = `${result.stdout}${result.stderr}`;
  for (const line of output.split('\n')) {
    const match = line.match(/^(?<path>[^:]+):(?<line>\d+): error: (?<message>.+?)(?:\s+\[[^\]]+\])?$/);
    if (match?.groups) {
      const { path, line: lineNumber, message } = match.groups;
      stdout.write(`::error file=${path},line=${lineNumber},title=MyPy::${message}\n`);
    }
  }
}

exit(result.status ?? 1);
