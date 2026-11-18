const { execFile } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);

// Compiles one .scad file to ASCII STL and returns it as { name, text }, the
// shape the viewer's upload input takes. Rejects with OpenSCAD's own error.
async function compileScad(scadPath, { openscad = 'openscad', timeoutMs = 120_000 } = {}) {
  const name = `${path.parse(scadPath).name}.stl`;
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'cad-a11y-scad-'));
  const out = path.join(dir, name);
  try {
    try {
      await execFileAsync(openscad, ['-o', out, '--export-format', 'asciistl', scadPath], {
        timeout: timeoutMs,
      });
    } catch (err) {
      throw new Error(openscadFailure(err, timeoutMs));
    }
    return { name, text: await fs.readFile(out, 'utf8') };
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

function openscadFailure(err, timeoutMs) {
  if (err.killed) return `OpenSCAD did not finish within ${timeoutMs / 1000} seconds.`;
  const stderr = String(err.stderr || '').trim();
  // A failed run also prints cache statistics; the ERROR lines are what to read out.
  const errors = stderr.split('\n').filter((line) => line.startsWith('ERROR'));
  return errors.join('\n') || stderr || err.message;
}

// Wraps an async job so it never runs twice at once. A trigger while it runs
// queues one rerun, however many triggers arrive, so the last change is built.
function serialize(job) {
  let state = 'idle'; // 'idle' | 'running' | 'rerun-queued'
  return async function trigger() {
    if (state !== 'idle') {
      state = 'rerun-queued';
      return;
    }
    try {
      do {
        state = 'running';
        await job();
      } while (state === 'rerun-queued');
    } finally {
      state = 'idle';
    }
  };
}

module.exports = { compileScad, serialize };
