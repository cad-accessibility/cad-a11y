const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { compileScad, serialize } = require('./compile');

const OPENSCAD = process.env.CAD_A11Y_OPENSCAD || 'openscad';
const noOpenscad = (() => {
  try {
    execFileSync(OPENSCAD, ['--version'], { stdio: 'ignore' });
    return false;
  } catch {
    return `${OPENSCAD} is not on PATH, so the compile tests cannot run`;
  }
})();

function scadFile(t, source) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cad-a11y-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'x.scad');
  fs.writeFileSync(file, source);
  return file;
}

test('compiles a scad file to an ASCII STL named after it', { skip: noOpenscad }, async (t) => {
  const model = await compileScad(scadFile(t, 'cube(1);\n'), { openscad: OPENSCAD });
  assert.equal(model.name, 'x.stl');
  assert.match(model.text, /^solid/);
  assert.match(model.text, /endsolid/);
});

test('a syntax error rejects with what OpenSCAD said', { skip: noOpenscad }, async (t) => {
  await assert.rejects(
    compileScad(scadFile(t, 'cube(1\n'), { openscad: OPENSCAD }),
    (err) => {
      assert.match(err.message, /error/i);
      assert.match(err.message, /x\.scad/);
      return true;
    },
  );
});

test('triggers during a run queue exactly one rerun', async () => {
  const runs = [];
  let finish;
  const trigger = serialize(() => {
    runs.push('run');
    return new Promise((resolve) => { finish = resolve; });
  });

  const first = trigger();
  assert.equal(runs.length, 1);
  trigger();
  trigger();
  trigger();
  assert.equal(runs.length, 1, 'no second run starts while the first is in flight');

  finish();
  await new Promise(setImmediate);
  assert.equal(runs.length, 2, 'the queued triggers become one rerun');
  finish();
  await first;
  assert.equal(runs.length, 2);

  const later = trigger();
  assert.equal(runs.length, 3, 'an idle trigger runs at once');
  finish();
  await later;
});

test('a failed run does not wedge later triggers', async () => {
  let calls = 0;
  const trigger = serialize(async () => {
    calls += 1;
    if (calls === 1) throw new Error('boom');
  });
  await assert.rejects(trigger(), /boom/);
  await trigger();
  assert.equal(calls, 2);
});
