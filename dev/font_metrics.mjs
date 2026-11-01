// Real text widths: canvas measureText in Chromium with the font files Excalidraw ships,
// which is exactly how Excalidraw measures text. Dev only; the skill itself never needs this.
//
//   node dev/font_metrics.mjs table             print the width table for whiteboard.py
//   node dev/font_metrics.mjs check FILE [...]  fail if whiteboard.py's table no longer matches the
//                                               fonts, or a stored text width is off in either direction
//
// Uses the renderer cache render.mjs sets up (EXACT=1 bash dev/smoke_test.sh runs that first).
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WHITEBOARD = path.join(HERE, '../skills/noteshub/scripts/whiteboard.py');
const CACHE = process.env.NOTESHUB_RENDER_CACHE ||   // same default as render.mjs
  path.join(os.homedir(), '.cache', 'noteshub-skills', 'excalidraw-render-0.18.0');
const FONT_ROOT = path.join(CACHE, 'node_modules/@excalidraw/excalidraw/dist/prod/fonts');

// Excalidraw font id -> [CSS family, folder in dist/prod/fonts]. Id 2 (Helvetica) ships no font
// file; Liberation Sans has the same metrics, so whiteboard.py and `check` use id 9 for it.
const FONTS = {
  1: ['Virgil', 'Virgil'], 3: ['Cascadia', 'Cascadia'], 5: ['Excalifont', 'Excalifont'],
  6: ['Nunito', 'Nunito'], 7: ['Lilita One', 'Lilita'], 8: ['Comic Shanns', 'ComicShanns'],
  9: ['Liberation Sans', 'Liberation'],
};
const family = id => FONTS[id === 2 ? 9 : id][0];

// A stored width may be this much wider than the real text before `check` calls it wrong.
const MAX_OVERSHOOT = 1.12;

function loadPlaywright() {
  for (const base of [import.meta.url, path.join(CACHE, 'package.json')]) {
    try { return createRequire(base)('playwright'); } catch { /* try next */ }
  }
  throw new Error(`Playwright not found; run render.mjs once to set up ${CACHE}`);
}

async function openFontPage() {
  const faces = Object.values(FONTS).flatMap(([name, dir]) =>
    fs.readdirSync(path.join(FONT_ROOT, dir)).filter(f => f.endsWith('.woff2')).map(f =>
      ({ name, b64: fs.readFileSync(path.join(FONT_ROOT, dir, f)).toString('base64') })));
  const browser = await loadPlaywright().chromium.launch();
  const page = await browser.newPage();
  await page.evaluate(async faces => {
    for (const { name, b64 } of faces) {
      const face = new FontFace(name, Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer);
      document.fonts.add(await face.load());
    }
    const ctx = document.createElement('canvas').getContext('2d');
    window.measure = (text, size, name) => {
      ctx.font = `${size}px "${name}"`;
      return ctx.measureText(text).width;
    };
  }, faces);
  return { browser, page };
}

// Advance widths in 1/1000 em for space..~, rounded up, as the Python source of whiteboard.py's table.
async function pythonTable(page) {
  const rows = [];
  for (const id of Object.keys(FONTS)) {
    const name = FONTS[id][0];
    const widths = await page.evaluate(name => Array.from({ length: 95 }, (_, i) =>
      Math.ceil(window.measure(String.fromCharCode(32 + i), 1000, name) - 0.01)), name);
    if (new Set(widths).size === 1) {
      rows.push(`    ${id}: (${widths[0]},) * 95,  # ${name}`);
      continue;
    }
    const lines = [];
    for (let i = 0; i < widths.length; i += 16) lines.push(widths.slice(i, i + 16).join(', '));
    rows.push(`    ${id}: (  # ${name}\n        ${lines.join(',\n        ')}),`);
  }
  return `_ASCII_WIDTHS = {\n${rows.join('\n')}\n}\n`;
}

const [command, ...files] = process.argv.slice(2);
if (command !== 'table' && !(command === 'check' && files.length)) {
  console.error('usage: node font_metrics.mjs table | check FILE [...]');
  process.exit(2);
}

const { browser, page } = await openFontPage();
let failures = 0;
try {
  const table = await pythonTable(page);
  if (command === 'table') {
    process.stdout.write(table);
  } else {
    if (!fs.readFileSync(WHITEBOARD, 'utf8').includes(table)) {
      failures++;
      console.log('FAIL whiteboard.py: _ASCII_WIDTHS differs from the fonts (node dev/font_metrics.mjs table)');
    }
    for (const file of files) {
      const scene = JSON.parse(execFileSync('python3', [WHITEBOARD, 'decode', file],
        { encoding: 'utf8', maxBuffer: 1 << 28 }));
      const texts = scene.elements.filter(e => e.type === 'text' && !e.isDeleted);
      let widest = 0;
      for (const e of texts) {
        const real = await page.evaluate(({ lines, size, name }) =>
          Math.max(...lines.map(line => window.measure(line, size, name))),
        { lines: e.text.split('\n'), size: e.fontSize, name: family(e.fontFamily) });
        const ratio = e.width / real;
        widest = Math.max(widest, ratio);
        // Only ASCII has real metrics, so only ASCII can be held to the upper bound.
        const tooWide = /^[\x20-\x7e\n]*$/.test(e.text) && ratio > MAX_OVERSHOOT;
        if (e.width < real || tooWide) {
          failures++;
          console.log(`FAIL ${file}: ${e.id} stored ${e.width.toFixed(2)}, real ${real.toFixed(2)}: ${e.text}`);
        }
      }
      console.log(`Measured ${file}: ${texts.length} texts, widest margin ${((widest - 1) * 100).toFixed(1)}%`);
    }
  }
} finally {
  await browser.close();
}
process.exit(failures ? 1 : 0);
