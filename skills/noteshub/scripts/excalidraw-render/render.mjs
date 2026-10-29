// Redraw NotesHub whiteboards with the real Excalidraw renderer (headless Chromium).
//
//   node render.mjs FILE.excalidraw.svg [...]   re-render each file in place, exactly like NotesHub saves
//   node render.mjs --verify FILE [...]         only load each file the way NotesHub does and report
//
// First run installs @excalidraw/excalidraw (same major version as NotesHub) into a cache
// folder (~150 MB, ~15 s) - set NOTESHUB_RENDER_CACHE to choose where. Needs Node.js 18+,
// network access to the npm registry, and Playwright with Chromium (used if already
// installed globally; otherwise installed into the cache, which downloads Chromium).
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const EXCALIDRAW_VERSION = '0.18.0';
const CACHE = process.env.NOTESHUB_RENDER_CACHE ||
  path.join(os.homedir(), '.cache', 'noteshub-skills', `excalidraw-render-${EXCALIDRAW_VERSION}`);
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const npx = process.platform === 'win32' ? 'npx.cmd' : 'npx';

function log(msg) { process.stderr.write(msg + '\n'); }

async function ensureBundle() {
  const bundle = path.join(CACHE, 'build', 'page.js');
  if (fs.existsSync(bundle)) return bundle;
  log(`Setting up the Excalidraw renderer in ${CACHE} (one time)...`);
  fs.mkdirSync(CACHE, { recursive: true });
  fs.writeFileSync(path.join(CACHE, 'package.json'), JSON.stringify({ name: 'noteshub-excalidraw-render', private: true }));
  execFileSync(npm, ['install', '--no-audit', '--no-fund', '--silent',
    `@excalidraw/excalidraw@${EXCALIDRAW_VERSION}`, 'react@19.1.0', 'react-dom@19.1.0', 'esbuild@0.24.0'],
    { cwd: CACHE, stdio: 'inherit' });
  fs.writeFileSync(path.join(CACHE, 'page-entry.js'),
    "import { loadFromBlob, exportToSvg } from '@excalidraw/excalidraw';\n" +
    'window.X = { loadFromBlob, exportToSvg }; window.__ready = true;\n');
  const esbuild = createRequire(path.join(CACHE, 'package.json'))('esbuild');
  await esbuild.build({
    entryPoints: [path.join(CACHE, 'page-entry.js')], bundle: true, format: 'esm', minify: true,
    outfile: bundle, loader: { '.woff2': 'file', '.ttf': 'file' }, logLevel: 'warning',
    define: { 'process.env.NODE_ENV': '"production"' },
  });
  return bundle;
}

function loadPlaywright() {
  for (const base of [import.meta.url, path.join(CACHE, 'package.json')]) {
    try { return createRequire(base)('playwright'); } catch { /* try next */ }
  }
  log('Playwright not found; installing it into the cache and downloading Chromium...');
  execFileSync(npm, ['install', '--no-audit', '--no-fund', '--silent', 'playwright'], { cwd: CACHE, stdio: 'inherit' });
  execFileSync(npx, ['playwright', 'install', 'chromium'], { cwd: CACHE, stdio: 'inherit' });
  return createRequire(path.join(CACHE, 'package.json'))('playwright');
}

function serve(bundle) {
  const assets = path.join(CACHE, 'node_modules/@excalidraw/excalidraw/dist/prod');
  const page = `<!doctype html><meta charset="utf-8"><script>window.EXCALIDRAW_ASSET_PATH='/assets/';</script>` +
    `<script type="module" src="/page.js"></script>`;
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(req.url.split('?')[0]);
    let file = null;
    if (url === '/') { res.writeHead(200, { 'content-type': 'text/html' }); return res.end(page); }
    if (url === '/page.js') file = bundle;
    else if (url.startsWith('/assets/')) file = path.join(assets, path.normalize(url.slice(8)).replace(/^(\.\.[/\\])+/, ''));
    else if (url.startsWith('/build/')) file = path.join(CACHE, path.normalize(url).replace(/^(\.\.[/\\])+/, ''));
    if (!file || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(r => server.listen(0, '127.0.0.1', () => r(server)));
}

const args = process.argv.slice(2);
const verifyOnly = args[0] === '--verify';
const files = verifyOnly ? args.slice(1) : args;
if (!files.length) {
  log('usage: node render.mjs [--verify] FILE.excalidraw.svg [...]');
  process.exit(2);
}

const bundle = await ensureBundle();
const { chromium } = loadPlaywright();
const server = await serve(bundle);
const browser = await chromium.launch();
let failures = 0;
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 120000 });
  for (const file of files) {
    const raw = fs.readFileSync(file, 'utf8');
    const result = await page.evaluate(async ({ raw, verifyOnly }) => {
      const type = raw.startsWith('{') ? 'application/vnd.excalidraw+json' : 'image/svg+xml';
      try {
        // The same two calls NotesHub makes: loadFromBlob on open, exportToSvg on save.
        const data = await window.X.loadFromBlob(new Blob([raw], { type }), null, null);
        const live = data.elements.filter(e => !e.isDeleted).length;
        if (verifyOnly) return { ok: true, live };
        const normalized = { elements: data.elements, files: data.files,
          appState: { ...data.appState, exportEmbedScene: true, exportBackground: false, exportScale: 1 } };
        const svg = await window.X.exportToSvg(normalized);
        return { ok: true, live, svg: svg.outerHTML };
      } catch (e) { return { ok: false, error: String((e && (e.code || e.message)) || e) }; }
    }, { raw, verifyOnly });
    if (!result.ok) { failures++; console.log(`FAIL ${file}: ${result.error}`); continue; }
    if (verifyOnly) { console.log(`OK   ${file}: ${result.live} elements`); continue; }
    if (!file.toLowerCase().endsWith('.excalidraw.svg')) { console.log(`SKIP ${file}: not an .excalidraw.svg file`); continue; }
    fs.writeFileSync(file, result.svg);
    console.log(`Rendered ${file} (${result.live} elements)`);
  }
} finally {
  await browser.close();
  server.close();
}
process.exit(failures ? 1 : 0);
