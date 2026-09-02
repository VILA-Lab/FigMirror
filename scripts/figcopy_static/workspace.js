// figcopy webui — workspace landing interactions.
//
//   - Each `.dropzone` accepts paste / drag-drop / click for its
//     `.dropzone-input` (the native <input type=file> that backs form
//     submission). For images we render a thumbnail; for data we render
//     a folded fingerprint (sha256 prefix + line count + size + first 3
//     lines) and *not* the full content.
//   - Global paste listener routes clipboard payload to whichever
//     dropzone has focus; clipboardData.items[0].getAsFile() works for
//     both screenshot-paste (gives an image blob) and table-paste (gives
//     a text/plain item).
//   - GET /api/runs.json every 3s, diff-update the run-bar list in place
//     (no full re-render).
//
// Vanilla, no deps. Loaded with `defer`, so DOM is parsed when this
// runs at top level.


/* ─────────── dropzones ─────────── */

const SHA_PREFIX_LEN = 12;          // chars of sha256 to display

// Track which dropzone the cursor is currently over. Cmd+V routing
// uses this in preference to focus, so the user can hover (no click)
// then paste — the standard pattern in image-upload UIs (Figma /
// Notion / Excalidraw all do this). Reverting to focus would mean
// clicking the zone first, which also opens the file picker — wrong.
let hoveredDropzone = null;

/**
 * Wire one dropzone. The native <input type=file> stays in the DOM as the
 * form-submission source of truth; we mirror paste / drag-drop into it.
 */
function setupDropzone(zone) {
  const input = zone.querySelector('.dropzone-input');
  const hint = zone.querySelector('.dropzone-hint');
  const preview = zone.querySelector('.dropzone-preview');
  const clearBtn = zone.querySelector('.dropzone-clear');
  const kind = zone.closest('.input-zone').dataset.kind;  // 'image' | 'data'

  // Click anywhere on the zone (except the clear button) → trigger native picker.
  zone.addEventListener('click', (e) => {
    if (e.target === input) return;            // native click, don't recurse
    if (e.target.closest('.dropzone-clear')) return;
    if (e.target.closest('.dropzone-preview')) return;
    input.click();
  });

  // Native input change.
  input.addEventListener('change', () => {
    if (input.files && input.files[0]) showPreview(input.files[0]);
  });

  // Drag-drop. Highlight zone on dragenter; accept files on drop.
  ['dragenter', 'dragover'].forEach(ev =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      zone.classList.add('is-dragging');
    })
  );
  ['dragleave', 'dragend'].forEach(ev =>
    zone.addEventListener(ev, (e) => {
      // Only un-highlight when leaving the zone itself, not a child.
      if (ev === 'dragleave' && zone.contains(e.relatedTarget)) return;
      zone.classList.remove('is-dragging');
    })
  );
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('is-dragging');
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) acceptFile(file);
  });

  // Track hover so the global paste listener can route Cmd+V to
  // whichever dropzone the cursor is currently over, with no click
  // required. mouseenter/leave (not mouseover/out) so we don't
  // bubble-thrash on inner elements.
  zone.addEventListener('mouseenter', () => { hoveredDropzone = zone; });
  zone.addEventListener('mouseleave', () => {
    if (hoveredDropzone === zone) hoveredDropzone = null;
  });

  // Clear button.
  clearBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    resetZone();
  });

  /**
   * Accept a File or Blob. Sets it on the native input via DataTransfer
   * so the form carries it on submit, then renders the preview.
   */
  function acceptFile(file) {
    if (kind === 'image' && !file.type.startsWith('image/')) {
      // Non-image dropped on the image zone. Bail loudly.
      flashError(zone, `expected an image, got ${file.type || 'unknown'}`);
      return;
    }
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
    } catch (_e) {
      // Older browsers — DataTransfer.items.add is well-supported in
      // Chrome/Firefox/Safari 14+, but if it ever fails we can't sync
      // the File into the input. Surface that.
      flashError(zone, 'browser does not support programmatic file paste');
      return;
    }
    showPreview(file);
  }

  function showPreview(file) {
    hint.hidden = true;
    preview.hidden = false;
    if (kind === 'image') {
      const img = preview.querySelector('.dropzone-img');
      if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
      const url = URL.createObjectURL(file);
      img.src = url;
      img.dataset.objectUrl = url;
    } else {
      renderFingerprint(file, preview);
    }
  }

  function resetZone() {
    input.value = '';
    if (kind === 'image') {
      const img = preview.querySelector('.dropzone-img');
      if (img.dataset.objectUrl) {
        URL.revokeObjectURL(img.dataset.objectUrl);
        delete img.dataset.objectUrl;
      }
      img.src = '';
    } else {
      preview.querySelectorAll('.fp-val').forEach(el => el.textContent = '—');
      const fpPre = preview.querySelector('.fp-preview');
      if (fpPre) fpPre.textContent = '';
    }
    preview.hidden = true;
    hint.hidden = false;
  }

  // expose for paste handler
  zone._figcopy = { kind, acceptFile };
}

/**
 * Compute and render a folded data fingerprint:
 *   lines: 1247
 *   size:  38.2 KB
 *   sha256: a1b2c3d4e5f6
 *   <first 3 lines>
 */
async function renderFingerprint(file, preview) {
  const sizeStr = humanSize(file.size);
  const set = (key, val) => {
    const el = preview.querySelector(`[data-fp="${key}"]`);
    if (el) el.textContent = val;
  };
  set('size', sizeStr);
  set('lines', '…');
  set('sha', '…');

  // First 3 lines + line count: read as text.
  let text;
  try {
    text = await file.text();
  } catch (_e) {
    set('lines', '?');
    return;
  }
  const allLines = text.split('\n');
  // If the file ends with a trailing newline, split yields an empty final
  // element; the line count people expect is "lines you'd `wc -l`", so
  // drop it.
  const lineCount = (allLines[allLines.length - 1] === '')
    ? allLines.length - 1 : allLines.length;
  set('lines', String(lineCount));
  const fpPre = preview.querySelector('.fp-preview');
  if (fpPre) fpPre.textContent = allLines.slice(0, 3).join('\n');

  // sha256: WebCrypto. ArrayBuffer of original bytes, not text reread.
  try {
    const buf = await file.arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', buf);
    const hex = Array.from(new Uint8Array(digest))
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');
    set('sha', hex.slice(0, SHA_PREFIX_LEN) + '…');
  } catch (_e) {
    set('sha', '?');
  }
}

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function flashError(zone, msg) {
  zone.classList.add('has-error');
  zone.dataset.errorMsg = msg;
  setTimeout(() => zone.classList.remove('has-error'), 1800);
  console.warn('[figcopy/dropzone]', msg);
}


/* ─────────── global paste handler ─────────── */

// Paste fires on document; we route to whichever dropzone the cursor
// is currently HOVERING — that's the ergonomic pattern (no click to
// arm; hover-then-Cmd+V). Falls back to the focused dropzone for
// keyboard-only users (Tab to dropzone, then paste).
//
// Critical guard: if the user has an <input>/<textarea> focused and
// is mid-typing, we must NOT hijack their paste even when the cursor
// happens to hover a dropzone — let the native paste land in their
// text field. Same for contenteditable.
document.addEventListener('paste', (e) => {
  const active = document.activeElement;
  // Bail if user is typing in a real text editor (TEXT-type input,
  // textarea, contenteditable). file inputs are NOT text editors —
  // they don't accept clipboard text/image natively, so a paste with
  // focus on a dropzone-input should still route to the surrounding
  // dropzone. Earlier guard treated all <input> as editable, which
  // regressed the after-cancel-picker flow.
  const inTextEditable = active && (
    (active.tagName === 'INPUT' && active.type !== 'file')
    || active.tagName === 'TEXTAREA'
    || active.isContentEditable);
  if (inTextEditable) return;
  const zone = hoveredDropzone
    || (active && active.closest && active.closest('.dropzone'));
  if (!zone || !zone._figcopy) return;
  const items = e.clipboardData && e.clipboardData.items;
  if (!items || items.length === 0) return;

  const expected = zone._figcopy.kind;  // 'image' | 'data'
  // Look for the matching item. For images: type starts with 'image/'.
  // For data: type is 'text/*' (table paste comes as text/plain or
  // text/tab-separated-values).
  for (const item of items) {
    if (expected === 'image' && item.type.startsWith('image/')) {
      const blob = item.getAsFile();
      if (blob) {
        e.preventDefault();
        // Pasted blobs lack a filename; synthesize one with the right ext.
        const ext = (blob.type.split('/')[1] || 'png').split(';')[0];
        const file = new File([blob], `pasted.${ext}`, { type: blob.type });
        zone._figcopy.acceptFile(file);
        return;
      }
    }
    if (expected === 'data' && item.kind === 'string'
        && item.type.startsWith('text/')) {
      e.preventDefault();
      item.getAsString((text) => {
        const file = new File([text], 'pasted.txt', { type: 'text/plain' });
        zone._figcopy.acceptFile(file);
      });
      return;
    }
  }
});


/* ─────────── live polling ─────────── */

// 3-second poll of /api/runs.json. Diff-update the run-bar list in
// place rather than re-rendering — this keeps focus, scroll position,
// and any in-flight image loads stable.

const POLL_INTERVAL_MS = 3000;
const STATUS_LABEL = {
  shipped: 'shipped',
  running: 'running',
  failed:  'failed',
  idle:    'idle',
};
const STATUS_PILL_CLASS = {
  shipped: 'good',
  running: 'neutral',
  failed:  'bad',
  idle:    'mute',
};

function iterLabel(status, currentIter, nIters) {
  if (status === 'running' && currentIter !== null && currentIter !== undefined) {
    return `iter ${currentIter + 1}`;  // 1-based for display
  }
  return `${nIters} iter${nIters !== 1 ? 's' : ''}`;
}

function buildRunBar(r) {
  const li = document.createElement('li');
  li.className = 'run-bar';
  li.dataset.name = r.name;
  applyRunBar(li, r);
  return li;
}

function applyRunBar(li, r) {
  li.dataset.status = r.status;
  li.dataset.nIters = String(r.n_iters);
  if (r.reason) {
    li.title = r.reason;
  } else {
    li.removeAttribute('title');
  }

  // Thumb
  let thumb = li.querySelector('.run-bar-thumb');
  const wantThumb = !!r.thumb_url;
  if (wantThumb) {
    if (!thumb || thumb.classList.contains('run-bar-thumb-empty')) {
      if (thumb) thumb.remove();
      thumb = document.createElement('a');
      thumb.className = 'run-bar-thumb';
      thumb.tabIndex = -1;
      const img = document.createElement('img');
      img.alt = '';
      img.loading = 'lazy';
      thumb.appendChild(img);
      li.prepend(thumb);
    }
    thumb.href = `/r/${encodeURIComponent(r.name)}`;
    const img = thumb.querySelector('img');
    if (img.getAttribute('src') !== r.thumb_url) {
      img.src = r.thumb_url;
    }
  } else {
    if (!thumb || !thumb.classList.contains('run-bar-thumb-empty')) {
      if (thumb) thumb.remove();
      thumb = document.createElement('span');
      thumb.setAttribute('aria-hidden', 'true');
      li.prepend(thumb);
    }
    const emptyState = r.status === 'running'
      ? 'loading'
      : (r.status === 'failed' ? 'failed' : 'idle');
    thumb.className =
      `run-bar-thumb run-bar-thumb-empty run-bar-thumb-${emptyState}`;
  }

  // Body (name + meta)
  let body = li.querySelector('.run-bar-body');
  if (!body) {
    body = document.createElement('div');
    body.className = 'run-bar-body';
    // Name-row wraps the backend badge + the run name link so they
    // share a line. Mirrors the server-rendered structure in
    // _render_run_bar so diff-updates don't visibly re-layout.
    const nameRow = document.createElement('div');
    nameRow.className = 'run-bar-name-row';
    const a = document.createElement('a');
    a.className = 'run-bar-name';
    nameRow.appendChild(a);
    body.appendChild(nameRow);
    const meta = document.createElement('span');
    meta.className = 'run-bar-meta';
    body.appendChild(meta);
    li.appendChild(body);
  }
  // data-backend attribute on the <li> so CSS / state-restorations
  // see which backend painted the run; mirrors the server tag.
  li.dataset.backend = r.backend || '';
  // Backend badge — small inline tag (Cx / Cl / Mk) in front of the
  // name. Idempotent: insert when missing, update when stale, remove
  // when the run has no backend (legacy runs predating the field).
  const nameRow = body.querySelector('.run-bar-name-row');
  let badge = nameRow.querySelector('.run-backend-badge');
  if (r.backend) {
    const labelMap = { codex: 'Cx', claude: 'Cl', mock: 'Mk' };
    const titleMap = {
      codex: 'Codex CLI', claude: 'Claude Code', mock: 'MockRunner',
    };
    const desiredLabel = labelMap[r.backend] || '';
    const desiredTitle = titleMap[r.backend] || r.backend;
    if (!badge) {
      badge = document.createElement('span');
      badge.className = `run-backend-badge run-backend-${r.backend}`;
      nameRow.insertBefore(badge, nameRow.firstChild);
    } else {
      badge.className = `run-backend-badge run-backend-${r.backend}`;
    }
    if (badge.textContent !== desiredLabel) badge.textContent = desiredLabel;
    if (badge.title !== desiredTitle) {
      badge.title = desiredTitle;
      badge.setAttribute('aria-label', `backend: ${desiredTitle}`);
    }
  } else if (badge) {
    badge.remove();
  }
  const a = body.querySelector('.run-bar-name');
  a.href = `/r/${encodeURIComponent(r.name)}`;
  if (a.textContent !== r.name) a.textContent = r.name;
  const meta = body.querySelector('.run-bar-meta');
  const newMetaText = iterLabel(r.status, r.current_iter, r.n_iters);
  if (meta.textContent !== newMetaText) meta.textContent = newMetaText;

  // Status pill — build via DOM, not innerHTML, because the fallback
  // `STATUS_LABEL[r.status] || r.status` can interpolate any string
  // the workdir's status.json sidecar happens to contain (status.json
  // is authored by the runner, but a misconfigured / hand-edited
  // sidecar with `<img src=x onerror=...>` would otherwise execute on
  // the next 3s poll).
  let statusWrap = li.querySelector('.run-bar-status');
  if (!statusWrap) {
    statusWrap = document.createElement('span');
    statusWrap.className = 'run-bar-status';
    li.appendChild(statusWrap);
  }
  const cls = STATUS_PILL_CLASS[r.status] || 'mute';
  const label = STATUS_LABEL[r.status] || r.status;
  // Replace the pill node rather than mutate textContent; this also
  // updates the class set when the status transitions.
  while (statusWrap.firstChild) statusWrap.removeChild(statusWrap.firstChild);
  const pill = document.createElement('span');
  pill.className = `pill ${cls}`;
  pill.textContent = label;
  statusWrap.appendChild(pill);

  // Delete button (×). Idempotent: insert when missing. The single
  // delegated click handler installed in setupRunListDelete() reads
  // `data-name` to know which run to nuke. Hidden until hover via
  // CSS so it doesn't compete with the name link's affordance.
  let delBtn = li.querySelector('.run-bar-delete');
  if (!delBtn) {
    delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'run-bar-delete';
    delBtn.title = 'Delete this run';
    delBtn.textContent = '×';
    li.appendChild(delBtn);
  }
  delBtn.dataset.name = r.name;
  delBtn.setAttribute('aria-label', `delete run ${r.name}`);
}

async function pollRuns() {
  let resp;
  try {
    resp = await fetch('/api/runs.json', { cache: 'no-store' });
  } catch (_e) {
    return;  // momentarily unreachable; next tick retries
  }
  if (!resp.ok) return;
  let payload;
  try {
    payload = await resp.json();
  } catch (_e) { return; }

  const runs = (payload && Array.isArray(payload.runs)) ? payload.runs : [];
  const list = document.getElementById('run-list');
  const emptyEl = document.getElementById('run-list-empty');
  const countEl = document.querySelector('[data-runs-count]');

  if (countEl) countEl.textContent = `· ${runs.length}`;

  if (!list) return;

  if (runs.length === 0) {
    list.innerHTML = '';
    list.dataset.empty = '1';
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  delete list.dataset.empty;
  if (emptyEl) emptyEl.hidden = true;

  // Diff-update: walk new runs in the order returned by the server
  // (newest first). For each, find existing <li data-name> and update
  // in place, or create a new one.
  const seen = new Set();
  let prevSibling = null;
  for (const r of runs) {
    seen.add(r.name);
    let li = list.querySelector(`li.run-bar[data-name="${cssEscape(r.name)}"]`);
    if (!li) {
      li = buildRunBar(r);
    } else {
      applyRunBar(li, r);
    }
    // Reorder: place after prevSibling (or at start).
    if (prevSibling) {
      if (prevSibling.nextSibling !== li) {
        list.insertBefore(li, prevSibling.nextSibling);
      }
    } else {
      if (list.firstChild !== li) list.insertBefore(li, list.firstChild);
    }
    prevSibling = li;
  }
  // Remove rows that no longer exist.
  Array.from(list.children).forEach(child => {
    if (child.dataset.name && !seen.has(child.dataset.name)) child.remove();
  });
}

// Minimal CSS.escape polyfill for attribute selectors. Modern browsers
// have CSS.escape; older ones don't. Run names are slugified server-side
// so this is really only defensive against quotes and odd chars.
function cssEscape(s) {
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/["\\]/g, '\\$&');
}


/* ─────────── async form submit ───────────
 *
 * Per user direction (2026-05-12): clicking Run should NOT navigate
 * away. The user wants to stay on the landing page and see the new
 * run appear in the right-hand list with a spinner so they can
 * keep submitting / monitoring without a page-reload. The server
 * supports this via an `Accept: application/json` POST which returns
 * `{name, url}` with HTTP 201 instead of the legacy 303 → /r/<name>.
 *
 * When the form has uploaded, we:
 *   1. POST via fetch() with FormData (same body as native submit)
 *   2. On 201, clear the form so the next run is easy to compose
 *   3. Force an immediate pollRuns() so the new card paints before
 *      the next 3s tick
 *   4. Highlight the new card briefly (data-just-added attr → CSS
 *      flash) so the user notices it
 *
 * Errors surface as a small banner above the submit button.
 */
function setupAsyncRunForm() {
  const form = document.getElementById('new-run-form');
  if (!form) return;
  const submitBtn = form.querySelector('button[type=submit]');
  // Inline status div: shown after submit, cleared on next interaction.
  let status = form.querySelector('.run-form-status');
  if (!status) {
    status = document.createElement('div');
    status.className = 'run-form-status';
    status.hidden = true;
    form.insertBefore(status, submitBtn);
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    // Disable the button + briefly show "submitting…" so a double-
    // click can't fire two POSTs (otherwise the user would see two
    // run cards appear, one of which is just a duplicate workdir).
    submitBtn.disabled = true;
    const origLabel = submitBtn.textContent;
    submitBtn.textContent = 'submitting…';
    status.hidden = true;
    status.classList.remove('run-form-status-err');

    let resp;
    try {
      resp = await fetch('/api/run', {
        method: 'POST',
        body: new FormData(form),
        headers: { 'Accept': 'application/json' },
      });
    } catch (err) {
      status.hidden = false;
      status.textContent = `network error: ${err && err.message || err}`;
      status.classList.add('run-form-status-err');
      submitBtn.disabled = false;
      submitBtn.textContent = origLabel;
      return;
    }
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const t = await resp.text();
        if (t) detail = `${detail}: ${t.slice(0, 200)}`;
      } catch (_e) {}
      status.hidden = false;
      status.textContent = detail;
      status.classList.add('run-form-status-err');
      submitBtn.disabled = false;
      submitBtn.textContent = origLabel;
      return;
    }
    let payload = {};
    try { payload = await resp.json(); } catch (_e) {}
    const newName = payload.name;

    // Clear the form so the next submission is clean. We don't reset
    // the name field — it's likely the user wants to vary just one
    // input across submissions.
    // Reset dropzones (image preview + data fingerprint).
    form.querySelectorAll('.dropzone-input').forEach(inp => { inp.value = ''; });
    form.querySelectorAll('.dropzone-preview').forEach(prev => {
      prev.hidden = true;
      const img = prev.querySelector('.dropzone-img');
      if (img) img.removeAttribute('src');
    });
    form.querySelectorAll('.dropzone-hint').forEach(hint => { hint.hidden = false; });
    const promptArea = form.querySelector('textarea[name=prompt]');
    if (promptArea) promptArea.value = '';
    const nameInput = form.querySelector('input[name=run_name]');
    if (nameInput) nameInput.value = '';

    submitBtn.disabled = false;
    submitBtn.textContent = origLabel;

    // Show a small confirmation, auto-hide after 3s.
    status.hidden = false;
    status.textContent = newName ? `started: ${newName}` : 'started';
    status.classList.remove('run-form-status-err');
    setTimeout(() => { status.hidden = true; }, 3000);

    // Force-poll so the new run appears immediately, then flash the
    // card so it visually stands out from the other rows.
    await pollRuns();
    if (newName) {
      const li = document.querySelector(
        `li.run-bar[data-name="${cssEscape(newName)}"]`
      );
      if (li) {
        li.dataset.justAdded = '1';
        setTimeout(() => { delete li.dataset.justAdded; }, 1800);
      }
    }
  });
}


/* ─────────── backend dropdown → footnote ───────────
 *
 * The footnote under the Run button describes whatever backend is
 * currently picked in the dropdown. Updates on page load and on
 * every dropdown change. Keeping the text per-backend (rather than
 * a single generic line) tells the user what process will actually
 * fire — important because the two CLIs differ in error surfaces,
 * cancellation semantics, and skill-registration paths.
 *
 * Keep these descriptions short and concrete. No "real" / "stub"
 * marketing copy.
 */
const BACKEND_NOTES = {
  codex: 'Powered by OpenAI Codex. The agent drafts, self-reviews, and iterates until the style matches your reference.',
  claude: 'Powered by Anthropic Claude. The agent drafts, self-reviews, and iterates until the style matches your reference.',
};

function setupBackendNote() {
  const sel = document.getElementById('run-backend');
  const note = document.getElementById('backend-note');
  if (!sel || !note) return;
  const sync = () => {
    const v = sel.value;
    note.innerHTML = BACKEND_NOTES[v] || '';
  };
  sel.addEventListener('change', sync);
  sync();
}


/* ─────────── runs-panel height sync ───────────
 *
 * The user's intent: left panel shows the form in full (no
 * scrollbar). The right panel's height is bounded by the left's
 * natural height; runs overflow the panel internally via a
 * vertical scrollbar, not by growing the page.
 *
 * CSS Grid can't express "right matches left and overflows
 * vertically when content exceeds": `align-items: stretch` would
 * grow the row to the taller of the two; `align-items: start`
 * lets each size to its own content (no cap on right). So we set
 * `max-height` on the right panel via JS, observing the left
 * panel's offsetHeight.
 *
 * Uses ResizeObserver so the sync re-fires when the form panel
 * grows (e.g. user pastes a tall data preview, drop-zone fills
 * in, etc.). Falls back to a window-resize listener.
 */
function setupRunsPanelHeightSync() {
  const layout = document.querySelector('.layout');
  if (!layout) return;
  const panels = layout.querySelectorAll('section.panel');
  if (panels.length < 2) return;
  const leftPanel = panels[0];
  const rightPanel = layout.querySelector('section.panel.run-panel') || panels[1];

  let last = 0;
  const sync = () => {
    const h = leftPanel.offsetHeight;
    if (h && h !== last) {
      rightPanel.style.maxHeight = `${h}px`;
      last = h;
    }
  };
  sync();
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(sync);
    ro.observe(leftPanel);
  }
  window.addEventListener('resize', sync);
  // Re-sync after page-load assets finish layouting (images, etc.).
  window.addEventListener('load', sync);
}


/* ─────────── run list: delete (×) handler ───────────
 *
 * One delegated click handler on the `<ul#run-list>` instead of
 * one-per-row, because rows come and go via the 3 s poll. Reads
 * `data-name` off the clicked button.
 *
 * Flow per click:
 *   1. confirm() — native dialog, simple, no extra modal infra.
 *   2. DELETE /api/runs/<name>.
 *   3. On 204 → remove the <li> from the DOM (don't wait for next
 *      poll, the visual snap is immediate). Also force-poll to keep
 *      the count + empty-state widget in sync.
 *   4. On 409 → the run was still running; surface a one-line
 *      message and DO NOT remove the <li>. User can cancel first.
 *   5. On 404 → row was already gone; remove the <li> silently.
 *   6. Anything else → surface the HTTP code + body snippet.
 */
function setupRunListDelete() {
  const list = document.getElementById('run-list');
  if (!list) return;
  list.addEventListener('click', async (e) => {
    const btn = e.target.closest('.run-bar-delete');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const name = btn.dataset.name;
    if (!name) return;
    const li = btn.closest('.run-bar');
    const status = li ? (li.dataset.status || '') : '';
    const isRunning = status === 'running';
    const prompt = isRunning
      ? `"${name}" is still running. Cancel + delete it?\n\n` +
        `(You'll need to cancel via the run page first; the ` +
        `server refuses to delete a live run.)`
      : `Delete run "${name}" and all its iter files? This cannot be undone.`;
    if (!confirm(prompt)) return;
    // Visual cue: dim the row while the request is in flight so
    // double-click doesn't double-fire.
    if (li) li.classList.add('run-bar-deleting');
    btn.disabled = true;
    let resp;
    try {
      resp = await fetch(`/api/runs/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      });
    } catch (err) {
      if (li) li.classList.remove('run-bar-deleting');
      btn.disabled = false;
      alert(`delete failed: ${err && err.message || err}`);
      return;
    }
    if (resp.status === 204 || resp.status === 404) {
      if (li) li.remove();
      // Refresh counters / empty-state.
      pollRuns();
      return;
    }
    if (li) li.classList.remove('run-bar-deleting');
    btn.disabled = false;
    if (resp.status === 409) {
      const text = await resp.text().catch(() => '');
      alert(
        `Can't delete: ${text.slice(0, 200) || 'run is still running'}`
      );
      return;
    }
    const detail = await resp.text().catch(() => '');
    alert(`delete failed: HTTP ${resp.status}${detail ? ' — ' + detail.slice(0, 160) : ''}`);
  });
}


/* ─────────── boot ─────────── */

document.querySelectorAll('.dropzone').forEach(setupDropzone);
setupAsyncRunForm();
setupBackendNote();
setupRunListDelete();
setupRunsPanelHeightSync();

// Kick the poll loop only if the run-list element is present (it's only
// rendered on the landing page).
if (document.getElementById('run-list')) {
  setInterval(pollRuns, POLL_INTERVAL_MS);
}
