import test from 'node:test';
import assert from 'node:assert/strict';
import { resizeColumns } from '../../../console/diagnostic-columns.js';

test('column resizing clamps both adjacent columns and keeps total width', () => {
  const widths = [400, 104, 84, 120, 64, 128];
  assert.deepEqual(resizeColumns(widths, 0, 500), [424, 80, 84, 120, 64, 128]);
  assert.deepEqual(resizeColumns(widths, 0, -500), [284, 220, 84, 120, 64, 128]);
  for (let i = 0; i < 5; i++) for (const delta of [-1000, -10, 0, 10, 1000]) {
    const next = resizeColumns(widths, i, delta);
    assert.equal(next.reduce((a, b) => a + b), widths.reduce((a, b) => a + b));
  }
  assert.deepEqual(resizeColumns(widths, 5, 10), widths);
});

test('naturally wide first column does not jump at the start of a drag', () => {
  const widths = [950, 104, 84, 120, 64, 128];
  assert.deepEqual(resizeColumns(widths, 0, 0), widths);
  assert.equal(resizeColumns(widths, 0, -10)[0], 940);
  assert.equal(resizeColumns(widths, 0, 10)[0], 950);
});
