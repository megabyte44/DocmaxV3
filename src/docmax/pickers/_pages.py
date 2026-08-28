"""The two HTML pages the pickers serve.

One file, because they share a stylesheet and a posting convention, and because
[ADR 0005](../../../docs/adr/0005-gui-pickers.md) budgeted the whole mechanism
at "one HTML file and ~100 lines of server code". Keeping the markup here rather
than in a data file also keeps ``pyproject.toml``'s ``package-data`` list as it
is: there is nothing to remember to ship.

## Rendering the page without pdf.js

ADR 0005 said "a bundled copy of pdf.js". These pages use the browser's **own**
PDF viewer instead, in an ``<embed>`` behind a transparent drawing surface.
[ADR 0019](../../../docs/adr/0019-picker-package-and-rendering.md) records the
change and the reasoning: every browser that can run this page already renders
PDFs, and vendoring a megabyte of minified JavaScript into a Python wheel to
duplicate that is a cost with no matching benefit.

**The backdrop is a backdrop.** The coordinates come from the drawing surface,
whose size is fixed to the page's true aspect ratio and whose mapping to points
is therefore exact. If a browser ignores ``view=Fit`` and renders the preview at
some other zoom, the picture behind the box is wrong but the numbers are not —
and the numbers are also shown, and editable, so a user is never reduced to
guessing.

## What a page may do

Display, and post one value back. These pages have no upload, no navigation, and
no route to a file. The document they display is served from a loopback port for
a few seconds by ``server.py``, which is the only thing they can talk to.
"""

from __future__ import annotations

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #16181d;
  --muted: #5b616e;
  --line: #d7dae1;
  --accent: #2563eb;
  --accent-fg: #ffffff;
  --surface: #f5f6f8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a;
    --fg: #e8eaed;
    --muted: #9aa1ad;
    --line: #2c3038;
    --accent: #4d84ff;
    --accent-fg: #0b0d10;
    --surface: #1c1f25;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header {
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
header h1 { font-size: 15px; margin: 0; font-weight: 600; }
header .meta { color: var(--muted); font-size: 13px; }
main {
  display: flex;
  gap: 20px;
  padding: 20px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  padding: 16px;
  min-width: 260px;
}
.panel h2 { font-size: 13px; margin: 0 0 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.stage { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.stage embed { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; pointer-events: none; }
.surface { position: absolute; inset: 0; cursor: crosshair; }
.box {
  position: absolute;
  border: 2px solid var(--accent);
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  display: none;
}
.field { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.field label { width: 62px; color: var(--muted); }
.field input {
  width: 110px; padding: 6px 8px; font: inherit;
  border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--fg);
}
.actions { display: flex; gap: 8px; margin-top: 16px; }
button {
  font: inherit; padding: 8px 14px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line); background: var(--bg); color: var(--fg);
}
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); font-weight: 600; }
button:disabled { opacity: .5; cursor: not-allowed; }
.hint { color: var(--muted); font-size: 13px; margin: 0 0 12px; }
.status { margin-top: 14px; color: var(--muted); min-height: 1.5em; }
ol.pages { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
ol.pages li {
  display: flex; align-items: center; gap: 8px;
  border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); padding: 8px 10px; cursor: grab; user-select: none;
}
ol.pages li.dragging { opacity: .4; }
ol.pages li .n { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 2.5em; }
ol.pages li button { padding: 2px 8px; line-height: 1.2; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
"""

_POST = """
function post(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}
function done(message) {
  document.getElementById('status').textContent = message;
  document.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
}
function cancel() {
  post('cancel', {}).then(function () { done('Cancelled. You can close this tab.'); });
}
"""


def _shell(title: str, body: str, script: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        f"{body}\n<script>{_POST}{script}</script>\n</body>\n</html>\n"
    )


def box_page(*, name: str, page_number: int, width: float, height: float) -> str:
    """The crop picker: drag a rectangle, or type one, and post it back.

    ``width`` and ``height`` are the page's true size in points. They are the
    only geometry the page is given, and every number it posts is derived from
    them — so a browser that renders the backdrop at an unexpected zoom cannot
    change what the picker returns.
    """
    body = f"""
<header>
  <h1>Choose a crop box</h1>
  <span class="meta">{_escape(name)} &middot; page {page_number} &middot;
    {_number(width)} &times; {_number(height)} pt</span>
</header>
<main>
  <div id="stage" class="stage">
    <embed src="document.pdf#page={page_number}&amp;toolbar=0&amp;navpanes=0&amp;scrollbar=0&amp;view=Fit"
           type="application/pdf">
    <div id="surface" class="surface"><div id="box" class="box"></div></div>
  </div>
  <div class="panel">
    <h2>The box</h2>
    <p class="hint">Drag on the page, or type the numbers. Points, 72 to the inch,
       measured from the <strong>bottom-left</strong> corner.</p>
    <div class="field"><label for="x">x</label><input id="x" type="number" step="1" value="0"></div>
    <div class="field"><label for="y">y</label><input id="y" type="number" step="1" value="0"></div>
    <div class="field"><label for="w">width</label><input id="w" type="number" step="1" value="{_number(width)}"></div>
    <div class="field"><label for="h">height</label><input id="h" type="number" step="1" value="{_number(height)}"></div>
    <div class="actions">
      <button id="use" class="primary" type="button">Use this box</button>
      <button id="cancel" type="button">Cancel</button>
    </div>
    <p id="status" class="status"></p>
  </div>
</main>
"""
    script = f"""
var PAGE_W = {width!r}, PAGE_H = {height!r};
var stage = document.getElementById('stage');
var surface = document.getElementById('surface');
var boxEl = document.getElementById('box');
var inputs = {{ x: document.getElementById('x'), y: document.getElementById('y'),
                w: document.getElementById('w'), h: document.getElementById('h') }};

// The stage is sized to the page's aspect ratio, so one scale factor converts
// both axes and a box drawn on screen maps exactly onto points.
function fit() {{
  var wide = Math.min(760, window.innerWidth - 340);
  var width = Math.max(280, wide);
  stage.style.width = width + 'px';
  stage.style.height = (width * PAGE_H / PAGE_W) + 'px';
  draw();
}}
function scale() {{ return PAGE_W / stage.clientWidth; }}
function round(v) {{ return Math.round(v * 10) / 10; }}

function current() {{
  return {{ x: parseFloat(inputs.x.value) || 0, y: parseFloat(inputs.y.value) || 0,
            width: parseFloat(inputs.w.value) || 0, height: parseFloat(inputs.h.value) || 0 }};
}}
function draw() {{
  var b = current(), s = scale();
  if (b.width <= 0 || b.height <= 0) {{ boxEl.style.display = 'none'; return; }}
  boxEl.style.display = 'block';
  boxEl.style.left = (b.x / s) + 'px';
  boxEl.style.width = (b.width / s) + 'px';
  boxEl.style.height = (b.height / s) + 'px';
  // Screen y counts down from the top; PDF y counts up from the bottom.
  boxEl.style.top = ((PAGE_H - b.y - b.height) / s) + 'px';
}}
Object.keys(inputs).forEach(function (k) {{ inputs[k].addEventListener('input', draw); }});

var origin = null;
surface.addEventListener('pointerdown', function (e) {{
  surface.setPointerCapture(e.pointerId);
  var r = surface.getBoundingClientRect();
  origin = {{ x: e.clientX - r.left, y: e.clientY - r.top }};
}});
surface.addEventListener('pointermove', function (e) {{
  if (!origin) return;
  var r = surface.getBoundingClientRect(), s = scale();
  var cx = Math.min(Math.max(e.clientX - r.left, 0), r.width);
  var cy = Math.min(Math.max(e.clientY - r.top, 0), r.height);
  var left = Math.min(origin.x, cx), top = Math.min(origin.y, cy);
  var w = Math.abs(cx - origin.x), h = Math.abs(cy - origin.y);
  inputs.x.value = round(left * s);
  inputs.y.value = round((r.height - top - h) * s);
  inputs.w.value = round(w * s);
  inputs.h.value = round(h * s);
  draw();
}});
function release() {{ origin = null; }}
surface.addEventListener('pointerup', release);
surface.addEventListener('pointercancel', release);

document.getElementById('use').addEventListener('click', function () {{
  var b = current();
  if (b.width <= 0 || b.height <= 0) {{
    document.getElementById('status').textContent = 'Draw a box, or type a width and height.';
    return;
  }}
  post('choice', b).then(function () {{ done('Sent. You can close this tab.'); }});
}});
document.getElementById('cancel').addEventListener('click', cancel);
window.addEventListener('resize', fit);
fit();
"""
    return _shell("Choose a crop box", body, script)


def order_page(*, name: str, pages: int) -> str:
    """The reorder picker: drag the pages into the order you want, and post it.

    The cards carry page *numbers*, not thumbnails. Rendering thumbnails would
    need either a rasteriser — which is a dependency ADR 0005 spent its whole
    argument avoiding — or the pdf.js copy ADR 0019 declined to vendor. The
    document itself is beside the list for reference.
    """
    items = "\n".join(
        f'    <li draggable="true" data-page="{n}"><span class="n">{n}</span>'
        f'<button type="button" data-move="up" aria-label="Move page {n} earlier">&uarr;</button>'
        f'<button type="button" data-move="down" aria-label="Move page {n} later">&darr;</button></li>'
        for n in range(1, pages + 1)
    )
    body = f"""
<header>
  <h1>Choose a page order</h1>
  <span class="meta">{_escape(name)} &middot; {pages} page(s)</span>
</header>
<main>
  <div class="panel" style="flex: 1 1 380px;">
    <h2>The order</h2>
    <p class="hint">Drag the pages, or use the arrows. Every page appears exactly
       once and that cannot change — a reorder that dropped one would be found far too late.</p>
    <ol id="pages" class="pages">
{items}
    </ol>
    <div class="actions">
      <button id="use" class="primary" type="button">Use this order</button>
      <button id="reset" type="button">Reset</button>
      <button id="cancel" type="button">Cancel</button>
    </div>
    <p id="status" class="status"></p>
    <p class="hint" style="margin-top:10px;">Result: <code id="preview"></code></p>
  </div>
  <div class="stage" style="flex: 1 1 420px; height: 70vh; min-width: 320px;">
    <embed src="document.pdf" type="application/pdf">
  </div>
</main>
"""
    script = """
var list = document.getElementById('pages');
var preview = document.getElementById('preview');
var original = Array.prototype.map.call(list.children, function (li) { return li; });

function order() {
  return Array.prototype.map.call(list.children, function (li) {
    return parseInt(li.dataset.page, 10);
  });
}
function refresh() { preview.textContent = order().join(','); }

list.addEventListener('click', function (e) {
  var button = e.target.closest('button[data-move]');
  if (!button) return;
  var li = button.closest('li');
  if (button.dataset.move === 'up' && li.previousElementSibling) {
    list.insertBefore(li, li.previousElementSibling);
  } else if (button.dataset.move === 'down' && li.nextElementSibling) {
    list.insertBefore(li.nextElementSibling, li);
  }
  refresh();
});

var dragged = null;
list.addEventListener('dragstart', function (e) {
  dragged = e.target.closest('li');
  if (dragged) { dragged.classList.add('dragging'); }
});
list.addEventListener('dragend', function () {
  if (dragged) { dragged.classList.remove('dragging'); }
  dragged = null;
  refresh();
});
list.addEventListener('dragover', function (e) {
  e.preventDefault();
  var over = e.target.closest('li');
  if (!over || !dragged || over === dragged) return;
  var after = over.getBoundingClientRect();
  var before = (e.clientX - after.left) > after.width / 2;
  list.insertBefore(dragged, before ? over.nextElementSibling : over);
});

document.getElementById('reset').addEventListener('click', function () {
  original.forEach(function (li) { list.appendChild(li); });
  refresh();
});
document.getElementById('use').addEventListener('click', function () {
  post('choice', { order: order() }).then(function () { done('Sent. You can close this tab.'); });
});
document.getElementById('cancel').addEventListener('click', cancel);
refresh();
"""
    return _shell("Choose a page order", body, script)


def _escape(value: str) -> str:
    """A filename is user data on its way into markup. It gets escaped."""
    from html import escape

    return escape(value, quote=True)


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


__all__ = ["box_page", "order_page"]
