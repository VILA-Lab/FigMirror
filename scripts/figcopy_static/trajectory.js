// figcopy webui — trajectory page interactions.
//
//   - Dock-magnification: cursor proximity scales each iter thumb via
//     a Gaussian falloff, written to a CSS variable on each thumb.
//   - Keyboard navigation: ←/→ moves a focus through the thumb strip;
//     Enter / Space activates the focused iter (sets URL hash).
//   - Hash routing: #iter-N expands the matching panel; clearing the
//     hash collapses it. Browser back works because we use location.hash.
//   - Lightbox: click any <figure> img to enlarge; T toggles compare
//     with reference; Esc closes.
//   - Export Code modal: opened by per-iter button; fetches highlighted
//     HTML from /api/runs/<name>/code/<iter> and injects into the modal
//     body. Copy button uses navigator.clipboard.
//   - Live polling of _state.json with auto-reload on iter change.
//
// Loaded with `defer` from /static-ui/trajectory.js. The page also
// emits a tiny synchronous bootstrap setting:
//
//     window.FIGCOPY_REF_URL = "<url>" | null
//
// before this script's deferred execution. We read it once at top.

const REF_URL = window.FIGCOPY_REF_URL || null;


/* ─────────── Compact header on scroll ─────────── */

// Once the user scrolls past ~120px, swap the header into a single-line
// "compact" layout (CSS handles the visual transition). Frees vertical
// viewport for the iter strip / expanded view while still keeping the
// breadcrumb + run name + step tabs reachable. Re-expands on scroll-up.
//
// Hysteresis (ENTER=120, EXIT=40) is essential here, not cosmetic: the
// transition flips `.brand`/`.run-path`/`.status` to display:none
// (instant) which collapses the header from ~150px to ~50px in a
// single frame. Browser scroll anchoring then nudges `scrollY` to keep
// the visible content stable across that ~100px layout shift — which
// with a single threshold could push `scrollY` back across it and
// re-toggle the class on the very next frame, producing the flicker
// the user reported. A 80px hysteresis gap is wider than the layout
// shift, so anchor-induced scroll adjustments stay inside the dead
// band and the state holds. Smooth scrolls into iter panels (hash
// routing → scrollIntoView) are the most reliable trigger pre-fix.
// Header fold/expand: USER-DRIVEN, no scroll-trigger.
//
// History: an earlier version auto-compacted the header on scroll
// past a threshold. It interacted badly with browser scroll-anchoring
// (header height change → scrollY adjust → cross opposite threshold
// → re-toggle → flicker loop). After three rounds of hysteresis /
// transitions / overflow-anchor patching it still felt fragile, so
// we switched to a plain click button per user direction.
//
// Click `#header-fold` → toggle `.is-compact` on `header.top`,
// persist to localStorage so the choice survives navigations.
(function setupHeaderFold() {
  const header = document.querySelector('header.top');
  const btn = document.getElementById('header-fold');
  if (!header || !btn) return;
  const STORAGE_KEY = 'figcopy:header-compact';
  // Server renders with `is-compact no-transition` — see render_html.
  // The server-default matches our JS default (true = compact) so the
  // typical page load has zero work and zero animation here.
  let compact = true;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) compact = stored === '1';
  } catch (_e) { /* localStorage disabled → stick with default */ }
  // If user preference differs from the server-rendered default, flip
  // the class. `no-transition` is still in effect so this toggle does
  // NOT animate — the user sees the header in their preferred state
  // from first paint, no slide.
  if (!compact) header.classList.remove('is-compact');
  btn.setAttribute('aria-pressed', compact ? 'true' : 'false');
  btn.title = compact ? 'Expand header' : 'Collapse header';
  // Strip the transition suppressor after two animation frames so any
  // future toggle (user click) still animates. Two rAFs guarantee
  // we're past the initial layout + paint commit.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => header.classList.remove('no-transition'));
  });
  btn.addEventListener('click', () => {
    compact = !compact;
    header.classList.toggle('is-compact', compact);
    btn.setAttribute('aria-pressed', compact ? 'true' : 'false');
    btn.title = compact ? 'Expand header' : 'Collapse header';
    try { localStorage.setItem(STORAGE_KEY, compact ? '1' : '0'); }
    catch (_e) { /* no-op */ }
  });
})();


/* ─────────── Lightbox + wheel zoom + drag pan ─────────── */

let lbCurrent = null;
// Zoom state: scale factor + pan offset (in viewport pixels).
// Reset on every open / src swap.
let lbZoom = 1;
let lbPanX = 0;
let lbPanY = 0;
// Set to true once mousemove during a pan drag has actually moved.
// The overlay's `onclick='closeLB()'` would otherwise fire when a
// drag-pan that started on the image releases outside it: the click
// event bubbles up to the lowest common ancestor (the overlay div),
// triggering close. closeLB() consumes this flag instead of closing.
let lbDragMoved = false;
const LB_ZOOM_MIN = 1;
const LB_ZOOM_MAX = 8;

const lbEl = document.getElementById('lb');
const lbImgEl = document.getElementById('lb-img');

function lbApplyTransform() {
  if (!lbImgEl) return;
  lbImgEl.style.transform =
    `translate(${lbPanX}px, ${lbPanY}px) scale(${lbZoom})`;
  if (lbEl) lbEl.classList.toggle('is-zoomed', lbZoom > 1.001);
}

function lbResetZoom() {
  lbZoom = 1;
  lbPanX = 0;
  lbPanY = 0;
  lbApplyTransform();
}

document.addEventListener('click', (e) => {
  // Iter thumb click is a different gesture (expand, not lightbox);
  // handled below in the iter-strip section.
  if (e.target.closest('.iter-thumb')) return;
  // Modal-internal images shouldn't open a nested lightbox.
  if (e.target.closest('.modal')) return;
  // Bind lightbox to <figure> images only — never to thumbs (those
  // are bare <img> inside a span).
  const fig = e.target.closest('figure');
  if (!fig) return;
  const img = e.target.closest('img');
  if (!img) return;
  e.preventDefault();
  lbCurrent = img.getAttribute('src');
  if (lbImgEl) lbImgEl.src = lbCurrent;
  lbResetZoom();
  if (lbEl) lbEl.classList.add('open');
});

function closeLB() {
  // If a pan drag just released, the overlay click that fires here is
  // the unintended end-of-drag, not a deliberate close. Swallow it.
  if (lbDragMoved) {
    lbDragMoved = false;
    return;
  }
  if (lbEl) lbEl.classList.remove('open');
  lbResetZoom();
}

function toggleRef() {
  if (!REF_URL || !lbImgEl) return;
  // Source the showing-state from the live img attribute instead of
  // caching it in a separate flag — the flag was only ever read here
  // and could drift if the src was swapped elsewhere. Use
  // getAttribute (the literal we wrote) rather than `.src` (which the
  // browser absolutizes against the page URL, breaking equality).
  const showingRef = lbImgEl.getAttribute('src') === REF_URL;
  lbImgEl.src = showingRef ? lbCurrent : REF_URL;
  lbResetZoom();  // fresh image deserves a fresh zoom state
}

// Wheel: zoom in/out, transform-origin tracks the cursor so the point
// under the mouse stays under the mouse. preventDefault stops the
// underlying page from scrolling while the lightbox is open.
if (lbEl && lbImgEl) {
  lbEl.addEventListener('wheel', (e) => {
    if (!lbEl.classList.contains('open')) return;
    e.preventDefault();
    // deltaY is positive when scrolling DOWN; flip so down = zoom out.
    const delta = -e.deltaY * 0.0018;
    const next = Math.max(LB_ZOOM_MIN,
                          Math.min(LB_ZOOM_MAX, lbZoom * (1 + delta)));
    if (next === lbZoom) return;
    // Zoom-toward-cursor: shift pan so the cursor's image-relative
    // position is preserved across the scale change.
    const r = lbImgEl.getBoundingClientRect();
    const cx = e.clientX - (r.left + r.width / 2);
    const cy = e.clientY - (r.top + r.height / 2);
    const factor = next / lbZoom - 1;
    lbPanX -= cx * factor;
    lbPanY -= cy * factor;
    lbZoom = next;
    // When zoomed back to 1×, recenter so we don't leave a stale offset.
    if (lbZoom <= LB_ZOOM_MIN + 0.001) { lbPanX = 0; lbPanY = 0; }
    lbApplyTransform();
  }, { passive: false });

  // Pan: mousedown on the (zoomed) image starts a drag; mousemove
  // updates pan; mouseup ends. Listen on document for move/up so the
  // drag survives leaving the image bounds.
  let dragStart = null;
  lbImgEl.addEventListener('mousedown', (e) => {
    if (lbZoom <= 1.001) return;
    e.preventDefault();
    dragStart = { x: e.clientX - lbPanX, y: e.clientY - lbPanY };
    lbDragMoved = false;  // a drag hasn't *moved* until mousemove fires
    lbImgEl.classList.add('is-panning');
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragStart) return;
    lbDragMoved = true;
    lbPanX = e.clientX - dragStart.x;
    lbPanY = e.clientY - dragStart.y;
    lbApplyTransform();
  });
  document.addEventListener('mouseup', () => {
    if (!dragStart) return;
    dragStart = null;
    lbImgEl.classList.remove('is-panning');
    // Note: we don't clear lbDragMoved here. The overlay click that
    // fires AFTER mouseup needs to read the flag to suppress close.
    // closeLB() clears it after consuming.
  });

  // Double-click resets zoom (without closing the lightbox).
  lbImgEl.addEventListener('dblclick', (e) => {
    e.preventDefault();
    e.stopPropagation();
    lbResetZoom();
  });
}


/* ─────────── Iter strip: dock magnification ─────────── */

const strip = document.querySelector('.iter-strip');
const thumbs = strip ? Array.from(strip.querySelectorAll('.iter-thumb')) : [];

// Gaussian: scale peaks at center under cursor, falls off symmetrically.
//   scale(dx) = 1 + AMPLITUDE * exp(-(dx/SIGMA)^2)
// AMPLITUDE = peak grow factor minus 1 (so 0.25 = 1.25× max).
// SIGMA = px from thumb-center where the scaling falls to ~37%.
// Initial 0.7 / 120 was way too aggressive — magnified thumbs grew to
// 1.7× and overflowed the strip's padding (the strip's overflow-x:auto
// implicitly coerces overflow-y to auto, so growth got clipped).
// Tuned to 0.25 / 80: only the thumb directly under the cursor visibly
// grows, neighbors get a subtle nudge, no overflow.
const DOCK_AMPLITUDE = 0.25;
const DOCK_SIGMA = 80;

function applyMagnify(cursorX) {
  for (const t of thumbs) {
    const r = t.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const dx = cursorX - cx;
    const scale = 1 + DOCK_AMPLITUDE * Math.exp(-(dx * dx) / (DOCK_SIGMA * DOCK_SIGMA));
    t.style.setProperty('--scale', scale.toFixed(3));
  }
}

function resetMagnify() {
  for (const t of thumbs) t.style.setProperty('--scale', '1');
}

if (strip) {
  strip.addEventListener('mousemove', (e) => applyMagnify(e.clientX));
  strip.addEventListener('mouseleave', resetMagnify);
  // Reduce-motion: skip magnification entirely.
  if (window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    strip.removeEventListener('mousemove', applyMagnify);
    resetMagnify();
  }
}


/* ─────────── Iter strip: click + keyboard nav ─────────── */

function activateIter(idx) {
  const thumb = thumbs[idx];
  if (!thumb) return;
  const n = thumb.dataset.iter;
  // Use replaceState if hash already matches, otherwise the hashchange
  // handler triggers the expand. Both go through syncFromHash().
  if (location.hash === `#iter-${n}`) {
    syncFromHash();  // re-show in case it was manually hidden
  } else {
    location.hash = `#iter-${n}`;
  }
  thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
}

if (strip) {
  strip.addEventListener('click', (e) => {
    const thumb = e.target.closest('.iter-thumb');
    if (!thumb) return;
    e.preventDefault();
    const idx = thumbs.indexOf(thumb);
    if (idx >= 0) activateIter(idx);
  });
}

// Keyboard nav: ←/→ moves focus through thumbs; Enter/Space activates.
// Tab is a normal browser focus traversal.
let focusedThumb = -1;

document.addEventListener('keydown', (e) => {
  // Modal handles its own keys.
  if (document.querySelector('.modal:not([hidden])')) return;

  if (e.key === 'Escape') {
    if (document.getElementById('lb')?.classList.contains('open')) {
      closeLB();
      return;
    }
    if (location.hash.startsWith('#iter-')) {
      // Collapse the expanded panel by clearing the hash.
      history.pushState('', document.title,
        window.location.pathname + window.location.search);
      syncFromHash();
      return;
    }
  }
  if (e.key === 't' || e.key === 'T') {
    if (document.getElementById('lb')?.classList.contains('open')) {
      e.preventDefault();
      toggleRef();
    }
    return;
  }
  if (!thumbs.length) return;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    // Only intercept when the user is on the strip (or no other input
    // is focused) — don't break form left/right caret movement.
    const ae = document.activeElement;
    const inEditable = ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA'
                              || ae.isContentEditable);
    if (inEditable) return;
    e.preventDefault();
    const dir = e.key === 'ArrowLeft' ? -1 : 1;
    focusedThumb = Math.max(0, Math.min(thumbs.length - 1,
                                        (focusedThumb < 0 ? 0 : focusedThumb) + dir));
    thumbs[focusedThumb].focus();
    thumbs[focusedThumb].scrollIntoView(
      { behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
  if (e.key === 'Enter' || e.key === ' ') {
    const ae = document.activeElement;
    if (ae && ae.classList.contains('iter-thumb')) {
      e.preventDefault();
      const idx = thumbs.indexOf(ae);
      if (idx >= 0) activateIter(idx);
    }
  }
});


/* ─────────── Hash routing: expand / collapse panel ─────────── */

function syncFromHash() {
  const m = location.hash.match(/^#iter-(\d+)$/);
  const allPanels = document.querySelectorAll('.iter-expanded');
  // Clear is-active on every thumb + reset aria-selected.
  for (const t of thumbs) {
    t.classList.remove('is-active');
    t.setAttribute('aria-selected', 'false');
  }
  if (!m) {
    allPanels.forEach(p => { p.hidden = true; });
    return;
  }
  const n = m[1];
  allPanels.forEach(p => { p.hidden = (p.dataset.iter !== n); });
  const target = document.querySelector(`.iter-expanded[data-iter="${cssEscape(n)}"]`);
  if (target) {
    requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
  // Mark the corresponding thumb as active + selected (the tablist
  // contract that screen-readers need to announce panel changes).
  const thumb = document.querySelector(`.iter-thumb[data-iter="${cssEscape(n)}"]`);
  if (thumb) {
    thumb.classList.add('is-active');
    thumb.setAttribute('aria-selected', 'true');
  }
}

window.addEventListener('hashchange', syncFromHash);
syncFromHash();  // on initial load (deep links)

function cssEscape(s) {
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/["\\]/g, '\\$&');
}


/* ─────────── Export Code modal ─────────── */

const codeModal = document.getElementById('code-modal');

function runNameFromUrl() {
  // /r/<name>  → return <name>. Returns null in single-run mode (no
  // /r/ prefix). The Export Code button is only useful in workspace
  // mode where /api/runs/<name>/code/<iter> exists.
  const m = location.pathname.match(/^\/r\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

// Focus restored on modal close so keyboard users go back to where
// they were (the iter expanded panel's "Export Code" button).
let codeModalReturnFocus = null;

async function openCodeModal(iterN) {
  if (!codeModal) return;
  const runName = runNameFromUrl();
  if (!runName) return;  // single-run mode: no API endpoint
  const meta = codeModal.querySelector('[data-modal-meta]');
  const body = codeModal.querySelector('[data-modal-body]');
  const copy = codeModal.querySelector('[data-modal-copy]');
  const closeBtn = codeModal.querySelector('.modal-close');
  if (meta) meta.textContent = `· iter ${iterN}`;
  if (body) body.innerHTML =
    '<div class="muted" style="padding:1rem">Loading…</div>';
  if (copy) copy.classList.remove('is-copied');
  // Remember focus to restore on close.
  codeModalReturnFocus = document.activeElement;
  codeModal.hidden = false;
  // Move focus inside the modal so screen-reader users land in
  // the right place + Esc handler reliably fires.
  if (closeBtn) closeBtn.focus();
  // Track which iter is open so Copy button knows what to copy.
  codeModal.dataset.iter = String(iterN);
  try {
    const url = `/api/runs/${encodeURIComponent(runName)}/code/${iterN}`;
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const html = await r.text();
    if (body) body.innerHTML = html;
  } catch (e) {
    if (body) body.innerHTML =
      `<div class="missing">failed to load code: ${escapeHtml(String(e))}</div>`;
  }
}

function closeCodeModal() {
  if (!codeModal) return;
  codeModal.hidden = true;
  // Restore focus to the trigger.
  if (codeModalReturnFocus && typeof codeModalReturnFocus.focus === 'function') {
    codeModalReturnFocus.focus();
  }
  codeModalReturnFocus = null;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[c]));
}

if (codeModal) {
  // Close on overlay click + close button + data-modal-close.
  codeModal.addEventListener('click', (e) => {
    if (e.target.closest('[data-modal-close]')) closeCodeModal();
  });
  // Esc closes.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !codeModal.hidden) {
      e.preventDefault();
      closeCodeModal();
    }
  });
  // Copy button.
  const copyBtn = codeModal.querySelector('[data-modal-copy]');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const body = codeModal.querySelector('[data-modal-body]');
      // Copy the plain text version (innerText strips spans).
      const text = body ? body.innerText : '';
      try {
        await navigator.clipboard.writeText(text);
        copyBtn.classList.add('is-copied');
        const orig = copyBtn.textContent;
        copyBtn.textContent = 'Copied ✓';
        setTimeout(() => {
          copyBtn.classList.remove('is-copied');
          copyBtn.textContent = orig;
        }, 1400);
      } catch (e) {
        copyBtn.textContent = 'Copy failed';
        setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1800);
      }
    });
  }
}

// Wire any iter-action[data-action="code"] button to openCodeModal.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.iter-action[data-action="code"]');
  if (!btn) return;
  e.preventDefault();
  openCodeModal(btn.dataset.iter);
});


/* ─────────── Step 1: multi-select baselines bar ─────────── */

const baselineMs = document.getElementById('baseline-multiselect');
if (baselineMs) {
  const cbs = Array.from(baselineMs.querySelectorAll('.baseline-ms-cb'));
  const go = document.getElementById('baseline-ms-go');
  const countEl = document.getElementById('baseline-ms-count');
  function picked() {
    return cbs.filter(c => c.checked).map(c => parseInt(c.value, 10))
              .filter(Number.isFinite).sort((a, b) => a - b);
  }
  function updateGo() {
    const p = picked();
    if (countEl) countEl.textContent = `(${p.length})`;
    if (p.length === 0) {
      go.disabled = true;
      go.textContent = 'Refine selected as a set →';
      return;
    }
    go.disabled = false;
    go.textContent = p.length === 1
      ? `Refine iter ${p[0]} →`
      : `Refine these ${p.length} iters as a set →`;
  }
  cbs.forEach(cb => cb.addEventListener('change', updateGo));
  go.addEventListener('click', () => {
    const p = picked();
    if (!p.length) return;
    const name = runNameFromUrl();
    const base = name ? `/r/${encodeURIComponent(name)}` : '';
    location.assign(`${base}?step=2&set=${p.join(',')}`);
  });
  // Pre-tick the LAST iter as the sensible default — that's typically
  // the shipped/best one. User can untick or add more.
  if (cbs.length) cbs[cbs.length - 1].checked = true;
  updateGo();
}


/* ─────────── Delete-run button ───────────
 *
 * Header-chrome "Delete run" button (workspace mode). Confirms,
 * sends DELETE /api/runs/<name>, and on 204 navigates back to the
 * workspace landing. Refusal cases:
 * - 409: the run is still running. Show a one-line message asking
 *   the user to cancel first. Stay on the page so cancel is reachable.
 * - 404: row already gone server-side. Still navigate home so the
 *   user isn't stuck on a dead page.
 */
(function setupRunDeleteButton() {
  const btn = document.getElementById('run-delete-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const name = btn.dataset.name;
    if (!name) return;
    // Try to read the current status from #status so we can word the
    // confirm prompt accurately ("this run is still running") and not
    // make a doomed DELETE just to see 409.
    const statusEl = document.getElementById('status');
    const currentStatus = (statusEl && statusEl.dataset.status) || '';
    const isRunning = currentStatus === 'running';
    const prompt = isRunning
      ? `"${name}" is still running. The server will refuse the ` +
        `delete until you cancel it. Try anyway?`
      : `Delete run "${name}" and all its iter files?\n\n` +
        `This cannot be undone.`;
    if (!confirm(prompt)) return;
    btn.disabled = true;
    btn.textContent = 'Deleting…';
    let resp;
    try {
      resp = await fetch(`/api/runs/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      });
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Delete run';
      alert(`delete failed: ${err && err.message || err}`);
      return;
    }
    if (resp.status === 204 || resp.status === 404) {
      // Navigate back to the workspace landing. (Single-run /
      // no-workspace mode would have skipped emitting the button
      // server-side, so we always have a workspace root to return
      // to here.)
      location.assign('/');
      return;
    }
    btn.disabled = false;
    btn.textContent = 'Delete run';
    if (resp.status === 409) {
      const text = await resp.text().catch(() => '');
      alert(`Can't delete: ${text.slice(0, 200) || 'run is still running'}`);
      return;
    }
    const detail = await resp.text().catch(() => '');
    alert(`delete failed: HTTP ${resp.status}${detail ? ' — ' + detail.slice(0, 160) : ''}`);
  });
})();


/* ─────────── Step 2: chat refinement ─────────── */

const step2Section = document.querySelector('.step2-section[data-template-iter]');
if (step2Section) {
  initStep2(step2Section);
}

function initStep2(section) {
  const runName = section.dataset.runName;
  const templateIter = section.dataset.templateIter;
  // Phase 3: multi-baseline. data-baseline-iters is a CSV of ints.
  // Falls back to [templateIter] for phase-2 legacy URLs.
  const baselineIters = (() => {
    const csv = section.dataset.baselineIters;
    if (csv) {
      return csv.split(',').map(s => parseInt(s.trim(), 10)).filter(Number.isFinite);
    }
    if (templateIter !== undefined) {
      const n = parseInt(templateIter, 10);
      return Number.isFinite(n) ? [n] : [];
    }
    return [];
  })();
  const form = document.getElementById('step2-form');
  // Belt-and-suspenders preventDefault, installed BEFORE any code that
  // might throw further down in initStep2. The full submit handler is
  // attached later via addEventListener (see ~line 1153) with the real
  // POST / appendMessage / setSending logic. This .onsubmit assignment
  // is purely a navigation-safety net: if any later line throws (bad
  // localStorage state on `loadState()`, exception in `renderHistory`,
  // etc.) and the full listener never attaches, the user's click on
  // Send would otherwise trigger the form's native submit. Because the
  // step2-form has no `action=` and the textarea has no `name=`, the
  // browser builds a new URL from the current path stripped of its
  // query string — landing the user on `/r/<name>` with no `step=2`,
  // which the server renders as Stage 1. Observed 2026-05-12 after a
  // refine 500-failed: the Stage-2 page "snapped back to Stage 1".
  // This guard makes that impossible regardless of JS state.
  if (form) form.onsubmit = (e) => { e.preventDefault(); };
  // Scroll-anchor state. Tracks whether the user *was* near the bottom
  // BEFORE the most-recent DOM mutation. We capture this just before
  // each appendMessage / ensureLiveBubble call; after the mutation, if
  // it was near-bottom, we re-pin to bottom. Otherwise we leave scroll
  // position alone.
  //
  // MUST be declared before any code path that calls renderHistory() —
  // i.e. before the `if (state.messages.length) renderHistory();` at
  // initStep2 start AND before `hydrateChatHistory()` which also fires
  // renderHistory. `let` is NOT hoisted, so declaring this later in
  // the function (where it logically belongs with the scroll helpers)
  // caused a TDZ ReferenceError on hydrate → initStep2 aborted →
  // form.addEventListener('submit') at line ~1153 never attached →
  // clicking Send did nothing. Observed live 2026-05-12.
  let wasNearBottomBeforeMutation = true;
  const msgInput = document.getElementById('step2-msg');
  const chat = document.getElementById('step2-chat');
  const chatEmpty = document.getElementById('step2-chat-empty');
  const controlsList = document.getElementById('step2-controls-list');
  const controlsEmpty = document.getElementById('step2-controls-empty');
  const currentImg = document.getElementById('step2-current');
  const STATIC_PREFIX = `/static/${encodeURIComponent(runName)}/`;

  // Compute the set_id (sha1[:8] of comma-joined sorted-dedup baseline
  // list). Must byte-match the server's compute_set_id in
  // figcopy_runner/interface.py — change there and here together.
  function computeSetId(iters) {
    const sorted = [...new Set(iters)].sort((a, b) => a - b);
    const canonical = sorted.join(',');
    // SubtleCrypto SHA-1 via Web Crypto. Returns a Promise; we resolve
    // synchronously here by using the cached result on the closure.
    return crypto.subtle.digest('SHA-1', new TextEncoder().encode(canonical))
      .then(buf => {
        const bytes = new Uint8Array(buf);
        let hex = '';
        for (let i = 0; i < 4; i++) {
          hex += bytes[i].toString(16).padStart(2, '0');
        }
        return hex;
      });
  }

  let setId = null;       // set asynchronously below
  let evtSource = null;   // EventSource for /chat/<set_id>/stream
  let highestSeq = 0;     // for client-side Last-Event-ID tracking

  // Legacy localStorage key (phase 2 cache). We keep this as a fallback
  // when the server can't reach (network drop) — phase 3 server is
  // authoritative when reachable.
  const storageKey = `figcopy:chat:${runName}:${baselineIters.join(',')}`;

  /** @type {{messages: Array, deltas: Object}} */
  let state = loadState() || { messages: [], deltas: {} };
  if (state.messages.length) renderHistory();

  // Phase 3 bootstrap: compute set_id, hydrate chat history from
  // server (authoritative across devices), then open SSE for live
  // updates from agent activity.
  if (baselineIters.length) {
    computeSetId(baselineIters).then(sid => {
      setId = sid;
      return hydrateChatHistory(sid);
    }).then((hydrated) => {
      // Server-side now pre-fills currentImg with a sensible src
      // (latest refine for this set, else first baseline). No JS
      // placeholder injection needed — the figure is visible from
      // first paint, no CLS. JS only swaps src on SSE
      // refine_complete via the no-op guard. (The `currentImg.hidden
      // = false` toggle that used to live here is gone; the server
      // emits the figure already-visible.)
      const lastRole = hydrated && hydrated.lastRole;
      openSse(setId, { replay: !(hydrated && lastRole === 'assistant') });
    }).catch(err => {
      console.warn('Phase-3 chat bootstrap failed', err);
    });
  }

  async function hydrateChatHistory(sid) {
    try {
      const r = await fetch(
        `/api/runs/${encodeURIComponent(runName)}/chat/${sid}`,
        { cache: 'no-store' },
      );
      if (!r.ok) return null;
      const entries = await r.json();
      if (!Array.isArray(entries) || entries.length === 0) return null;
      // Replace any localStorage-cached state with server truth.
      state = { messages: [], deltas: {} };
      for (const e of entries) {
        if (e.role === 'user') {
          state.messages.push({
            role: 'user',
            text: e.content || '',
          });
        } else if (e.role === 'assistant') {
          const delta = e.rcparams_delta || {};
          state.messages.push({
            role: 'ai',
            text: '',
            review: e.review || e.content || '',
            delta,
            image_url: e.image_url || null,
          });
          for (const [k, v] of Object.entries(delta)) {
            state.deltas[k] = v;
          }
        }
        if (typeof e.seq === 'number' && e.seq > highestSeq) {
          highestSeq = e.seq;
        }
      }
      // Re-render from scratch using the rehydrated state.
      chat.innerHTML = '';
      if (chatEmpty) { chat.appendChild(chatEmpty); chatEmpty.hidden = true; }
      // Tear down + rebuild controls panel (clear any stale rows).
      if (controlsList) controlsList.innerHTML = '';
      renderHistory();
      saveState();
      return {
        count: entries.length,
        lastRole: entries[entries.length - 1]?.role || null,
      };
    } catch (err) {
      // Server unreachable — keep the localStorage state.
      return null;
    }
  }

  // While a turn is in flight, we render a transient "live" bubble that
  // accumulates the agent's text deltas + lists its tool calls. When
  // the refine_complete event arrives we promote the live bubble into a
  // final assistant message (with image + delta) and clear the live
  // pointer. If the page reloads mid-turn, hydration just picks up
  // whatever turns have completed; the in-flight bubble is lost but the
  // SSE replay will rebuild it on reconnect (server keeps events).
  let liveBubble = null;        // {el, textEl, toolsEl, text, tools}
  function ensureLiveBubble() {
    if (liveBubble) return liveBubble;
    const el = document.createElement('div');
    el.className = 'step2-msg-ai step2-msg-live';
    el.innerHTML =
      `<span class="step2-msg-role">ai</span>` +
      `<div class="step2-msg-body">` +
        `<p class="step2-msg-text-live"></p>` +
        `<div class="step2-msg-tools-live" hidden></div>` +
        `<span class="step2-msg-spinner" aria-label="agent working">` +
          `<span></span><span></span><span></span></span>` +
      `</div>`;
    if (chatEmpty) chatEmpty.hidden = true;
    rememberScrollPos();
    chat.appendChild(el);
    scrollChatToBottom();
    liveBubble = {
      el,
      textEl: el.querySelector('.step2-msg-text-live'),
      toolsEl: el.querySelector('.step2-msg-tools-live'),
      text: '',
      tools: new Map(),  // call_id → {name, status}
    };
    return liveBubble;
  }
  function clearLiveBubble() {
    if (!liveBubble) return;
    if (liveBubble.el.parentNode) liveBubble.el.remove();
    liveBubble = null;
  }
  function renderLiveTools() {
    if (!liveBubble) return;
    if (!liveBubble.tools.size) {
      liveBubble.toolsEl.hidden = true;
      return;
    }
    liveBubble.toolsEl.hidden = false;
    liveBubble.toolsEl.innerHTML = '';
    for (const [, info] of liveBubble.tools) {
      const row = document.createElement('div');
      row.className = `step2-tool-row step2-tool-${info.status}`;
      const dot = info.status === 'running' ? '◌' :
                  info.status === 'ok'      ? '✓' :
                                              '✗';
      row.textContent = `${dot} ${info.name}`;
      liveBubble.toolsEl.appendChild(row);
    }
  }

  function openSse(sid, opts = {}) {
    if (evtSource) {
      try { evtSource.close(); } catch (_e) {}
    }
    let url = `/api/runs/${encodeURIComponent(runName)}/chat/${sid}/stream`;
    if (opts.replay === false) {
      url += '?replay=0';
    }
    evtSource = new EventSource(url);

    evtSource.addEventListener('turn_start', () => {
      ensureLiveBubble();
    });

    evtSource.addEventListener('text', (e) => {
      try {
        const data = JSON.parse(e.data);
        const t = data.text || '';
        const lb = ensureLiveBubble();
        rememberScrollPos();
        lb.text += t;
        lb.textEl.textContent = lb.text;
        scrollChatToBottom();
      } catch (_e) {}
    });

    evtSource.addEventListener('tool_call_start', (e) => {
      try {
        const data = JSON.parse(e.data);
        const lb = ensureLiveBubble();
        const id = data.call_id || `t${lb.tools.size}`;
        // Friendly label: prefer the args' command/path, else name
        const args = data.args || {};
        const name = (args.command || args.changes || data.name || 'tool');
        const label = typeof name === 'string'
          ? name.replace(/^\/bin\/bash -lc /, '').slice(0, 80)
          : (args.changes ? `file: ${(args.changes[0]?.path)||'?'}` : data.name);
        lb.tools.set(id, { name: label, status: 'running' });
        renderLiveTools();
      } catch (_e) {}
    });

    evtSource.addEventListener('tool_call_end', (e) => {
      try {
        const data = JSON.parse(e.data);
        const lb = ensureLiveBubble();
        const id = data.call_id;
        const existing = lb.tools.get(id);
        if (existing) {
          existing.status = data.ok ? 'ok' : 'err';
        } else {
          lb.tools.set(id || `t${lb.tools.size}`, {
            name: data.result?.command || data.result?.type || 'tool',
            status: data.ok ? 'ok' : 'err',
          });
        }
        renderLiveTools();
      } catch (_e) {}
    });

    evtSource.addEventListener('refine_complete', (e) => {
      try {
        const data = JSON.parse(e.data);
        const seq = parseInt(e.lastEventId || '0', 10);
        if (Number.isFinite(seq)) highestSeq = Math.max(highestSeq, seq);
        const newImageUrl = data.image_url
          ? (STATIC_PREFIX + data.image_url)
          : null;
        // Dedupe: if the last assistant message in state already has
        // this image_url, our postRefine response handler beat the SSE
        // event — skip the duplicate append.
        const lastAi = [...state.messages].reverse().find(m => m.role === 'ai');
        if (!(lastAi && lastAi.image_url === newImageUrl)) {
          clearLiveBubble();
          appendMessage({
            role: 'ai',
            text: '',
            review: data.review || '',
            delta: data.rcparams_delta || {},
            image_url: newImageUrl,
          });
          for (const [k, v] of Object.entries(data.rcparams_delta || {})) {
            state.deltas[k] = v;
            ensureControl(k, v);
          }
        } else {
          clearLiveBubble();
        }
        if (newImageUrl && currentImg) {
          const img = currentImg.querySelector('img');
          if (img && img.dataset.currentUrl !== newImageUrl) {
            // Avoid no-op src reassignment that re-flashes the image.
            img.dataset.currentUrl = newImageUrl;
            img.src = newImageUrl;
          }
          currentImg.hidden = false;
        }
        saveState();
      } catch (_e) {}
    });

    evtSource.addEventListener('turn_end', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data && data.status && data.status !== 'completed') {
          if (!liveBubble) return;
          clearLiveBubble();
          appendMessage({
            role: 'system',
            text: `turn ${data.status}${data.reason ? ': ' + data.reason : ''}`,
          });
          setSending(false);
        }
      } catch (_e) {}
    });

    evtSource.addEventListener('history_truncated', () => {
      // Server's ring buffer dropped events we needed. Re-hydrate.
      if (setId) hydrateChatHistory(setId);
    });

    evtSource.onerror = () => {
      // Browser auto-reconnects EventSource. Nothing to do; the
      // reconnect carries our Last-Event-ID automatically.
    };
  }

  // Right-panel: click-and-hold (or click toggle) to see the previous
  // refine output side-by-side with current. Releases on mouseup or
  // a second click.
  if (currentImg) {
    const cimg = currentImg.querySelector('img');
    let savedSrc = null;
    function showPrevious() {
      if (!cimg || !cimg.src) return;
      // Parse refine_NNN.png from current src; show refine_(N-1).png
      // if it exists, else fall back to first baseline iter image.
      const m = cimg.src.match(/refine_(\d{3})\.png(\?.*)?$/);
      if (m) {
        const n = parseInt(m[1], 10);
        if (n > 1) {
          const prev = cimg.src.replace(/refine_\d{3}/, `refine_${String(n - 1).padStart(3, '0')}`);
          savedSrc = cimg.src;
          cimg.src = prev;
          currentImg.classList.add('step2-current-prev');
          return;
        }
      }
      // No prior refine — show first baseline.
      const first = baselineIters[0];
      if (Number.isFinite(first)) {
        savedSrc = cimg.src;
        cimg.src = `${STATIC_PREFIX}img_iter${first}.png`;
        currentImg.classList.add('step2-current-prev');
      }
    }
    function restoreCurrent() {
      if (savedSrc !== null && cimg) {
        cimg.src = savedSrc;
        savedSrc = null;
        currentImg.classList.remove('step2-current-prev');
      }
    }
    currentImg.addEventListener('mousedown', (e) => { e.preventDefault(); showPrevious(); });
    currentImg.addEventListener('mouseup',   restoreCurrent);
    currentImg.addEventListener('mouseleave', restoreCurrent);
    currentImg.addEventListener('touchstart', (e) => { e.preventDefault(); showPrevious(); }, {passive: false});
    currentImg.addEventListener('touchend',   restoreCurrent);
    currentImg.setAttribute('title', 'press-and-hold to see the previous version');
    currentImg.style.cursor = 'pointer';
  }

  window.addEventListener('beforeunload', () => {
    if (evtSource) try { evtSource.close(); } catch (_e) {}
  });

  function loadState() {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      if (!Array.isArray(parsed.messages)) parsed.messages = [];
      if (!parsed.deltas || typeof parsed.deltas !== 'object') parsed.deltas = {};
      return parsed;
    } catch (_e) { return null; }
  }

  function saveState() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
    } catch (_e) {
      // localStorage may be disabled (private browsing) — no-op.
    }
  }

  function renderHistory() {
    if (chatEmpty) chatEmpty.hidden = true;
    for (const m of state.messages) {
      appendMessageEl(m);
    }
    // Re-render controls for accumulated deltas.
    for (const [param, value] of Object.entries(state.deltas)) {
      ensureControl(param, value);
    }
    // Last image, if any.
    const lastAi = [...state.messages].reverse().find(m => m.role === 'ai' && m.image_url);
    if (lastAi && currentImg) {
      const img = currentImg.querySelector('img');
      if (img && img.dataset.currentUrl !== lastAi.image_url) {
        img.dataset.currentUrl = lastAi.image_url;
        img.src = lastAi.image_url;
      }
      currentImg.hidden = false;
    }
    // Initial render / hydration: always land at bottom regardless of
    // the current scroll position (the prior `wasNearBottom` snapshot
    // is meaningless before content existed).
    wasNearBottomBeforeMutation = true;
    scrollChatToBottom();
  }

  function appendMessage(m) {
    state.messages.push(m);
    saveState();
    if (chatEmpty) chatEmpty.hidden = true;
    rememberScrollPos();
    appendMessageEl(m);
    scrollChatToBottom();
  }

  function appendMessageEl(m) {
    const el = document.createElement('div');
    el.className = `step2-msg-${m.role}`;
    if (m.role === 'user') {
      el.innerHTML = `<span class="step2-msg-role">you</span>` +
        `<div class="step2-msg-body">${escapeHtml(m.text || '')}</div>`;
    } else if (m.role === 'ai') {
      const review = m.review || '';
      const delta = m.delta || {};
      const deltaSummary = Object.keys(delta).length
        ? `<div class="step2-msg-delta">${
            Object.entries(delta)
              .map(([k, v]) => `<code>${escapeHtml(k)}=${escapeHtml(String(v))}</code>`)
              .join(' ')
          }</div>`
        : '';
      const imageEl = m.image_url
        ? `<img class="step2-msg-img" src="${escapeHtml(m.image_url)}" alt="refinement render" loading="lazy">`
        : '';
      el.innerHTML = `<span class="step2-msg-role">ai</span>` +
        `<div class="step2-msg-body">` +
        `<p class="step2-msg-review">${escapeHtml(review)}</p>` +
        deltaSummary + imageEl +
        `</div>`;
    } else if (m.role === 'system') {
      el.innerHTML = `<div class="step2-msg-system">${escapeHtml(m.text || '')}</div>`;
    }
    chat.appendChild(el);
  }

  // "Near bottom" threshold for sticky auto-scroll. Anything within
  // STICKY_PX of the bottom edge counts as "user is following along"
  // and we'll keep them pinned to bottom when new content arrives.
  // If the user has scrolled further up than this, they're reading
  // history — DON'T yank them back to the bottom every time SSE
  // delivers a new chunk.
  const STICKY_PX = 60;
  function isNearBottom() {
    return chat.scrollHeight - chat.scrollTop - chat.clientHeight <= STICKY_PX;
  }
  function rememberScrollPos() {
    wasNearBottomBeforeMutation = isNearBottom();
  }
  function scrollChatToBottom() {
    // Only re-pin if the user was already at/near the bottom.
    // Initial-render + hydration paths set wasNearBottomBeforeMutation
    // = true explicitly so the first paint always lands at the bottom.
    if (wasNearBottomBeforeMutation) {
      chat.scrollTop = chat.scrollHeight;
    }
  }

  // ── Pending adjustments: staged locally; sent with the NEXT Send ──
  // Per user direction: changing a stepper / control should NOT
  // immediately fire a refine. It accumulates here and rides along
  // on the next message submit. The "X pending" badge near Send
  // shows the count.
  const pendingAdjustments = {};   // { paramName: parsedValue, ... }

  function updatePendingBadge() {
    const badge = document.getElementById('step2-pending-badge');
    if (!badge) return;
    const n = Object.keys(pendingAdjustments).length;
    if (n === 0) {
      badge.hidden = true;
      badge.textContent = '';
    } else {
      badge.hidden = false;
      badge.textContent = `${n} pending`;
      badge.title = Object.entries(pendingAdjustments)
        .map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join('\n');
    }
  }

  function parseControlInputValue(raw, originalValue) {
    // Try to match the SHAPE of originalValue: if it was a number,
    // parse number; if a bool, parse bool; if an array (comma-joined
    // for display), split + trim; else keep string.
    raw = (raw == null) ? '' : String(raw).trim();
    if (typeof originalValue === 'number') {
      const n = parseFloat(raw);
      return Number.isFinite(n) ? n : raw;
    }
    if (typeof originalValue === 'boolean') {
      if (/^(true|t|1|yes|y)$/i.test(raw)) return true;
      if (/^(false|f|0|no|n)$/i.test(raw)) return false;
      return raw;
    }
    if (Array.isArray(originalValue)) {
      return raw.split(',').map(s => s.trim()).filter(Boolean);
    }
    // Plain string. Numeric-looking strings stay as numbers if the
    // user typed a number (intent matters more than original type).
    const maybeNum = parseFloat(raw);
    if (Number.isFinite(maybeNum) && String(maybeNum) === raw) {
      return maybeNum;
    }
    return raw;
  }

  function formatControlDisplay(value) {
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (value == null) return '';
    return String(value);
  }

  function ensureControl(param, value) {
    // Surfaces an editable text-input row for the param. Numbers get
    // type=number (browser shows steppers); booleans/arrays/strings
    // get type=text. All rows share one change-listener that just
    // stages the value into pendingAdjustments.
    if (!controlsList) return;
    let row = controlsList.querySelector(`[data-param="${cssEscape(param)}"]`);
    const display = formatControlDisplay(value);
    const isNumeric = typeof value === 'number'
      || (typeof value === 'string' && Number.isFinite(parseFloat(value))
          && String(parseFloat(value)) === value.trim());
    if (!row) {
      row = document.createElement('div');
      row.className = 'step2-control';
      row.dataset.param = param;
      // Stash the original value's type so we can parse the user's
      // edit back into the same shape.
      row.dataset.originalValue = JSON.stringify(value);
      const inputType = isNumeric ? 'number' : 'text';
      row.innerHTML =
        `<label class="step2-control-label" for="ctrl-${cssEscape(param)}">` +
        `${escapeHtml(param)}</label>` +
        `<input type="${inputType}" class="step2-control-input" ` +
        `id="ctrl-${cssEscape(param)}" data-param="${escapeHtml(param)}" ` +
        `value="${escapeHtml(display)}"` +
        (isNumeric ? ' step="any"' : '') + '>';
      controlsList.appendChild(row);
      controlsEmpty.hidden = true;
    } else {
      const input = row.querySelector('.step2-control-input');
      // Don't yank from under a user who's actively editing OR who has
      // a staged edit pending OR whose row is marked dirty (in case
      // pendingAdjustments was cleared by a just-fired submit but the
      // user has already started typing the next adjustment before the
      // new server value lands here). Belt + suspenders.
      const isDirty = row.classList.contains('step2-control-pending');
      if (input && document.activeElement !== input
                && !(param in pendingAdjustments)
                && !isDirty) {
        input.value = display;
        row.dataset.originalValue = JSON.stringify(value);
      } else if (input && !isDirty && !(param in pendingAdjustments)) {
        // User is focused but hasn't started editing (or just blurred
        // without changing) — still safe to update originalValue so
        // a subsequent edit's "same-as-original" check uses the
        // latest server value.
        row.dataset.originalValue = JSON.stringify(value);
      }
    }
  }

  // Single listener — stages every control change locally; never fires
  // a refine on its own. Visible feedback: pending-count badge near Send.
  if (controlsList) {
    controlsList.addEventListener('change', (e) => {
      const input = e.target.closest('.step2-control-input');
      if (!input) return;
      const row = input.closest('.step2-control');
      const param = input.dataset.param;
      let original;
      try { original = JSON.parse(row.dataset.originalValue || 'null'); }
      catch (_e) { original = null; }
      const parsed = parseControlInputValue(input.value, original);
      // Drop the staged entry if the user edited back to the original.
      const sameAsOriginal = (typeof parsed === typeof original)
        && JSON.stringify(parsed) === JSON.stringify(original);
      if (sameAsOriginal) {
        delete pendingAdjustments[param];
        row.classList.remove('step2-control-pending');
      } else {
        pendingAdjustments[param] = parsed;
        row.classList.add('step2-control-pending');
      }
      updatePendingBadge();
    });
  }
  // The form-submit handler below reads pendingAdjustments and includes
  // it in the POST. On success it's cleared in handleAiResult.
  let latestRefineRequestId = 0;

  // Form submit (NL message). Same requestId guard as the structured-
  // control path so a rapidly-typed NL submit + a subsequent stepper
  // tweak (or vice-versa) can't apply each other's responses out of
  // order.
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = msgInput.value.trim();
    const havePending = Object.keys(pendingAdjustments).length > 0;
    if (!msg && !havePending) return;
    // Snapshot the staged adjustments so they're cleared from the UI
    // immediately on submit (rows lose the dirty highlight). They're
    // restored if the POST fails (caught below).
    const adjustmentsSnapshot = havePending ? { ...pendingAdjustments } : null;
    if (havePending) {
      // Visual: clear dirty highlights now; if POST fails we'll restore.
      Object.keys(pendingAdjustments).forEach(p => delete pendingAdjustments[p]);
      controlsList?.querySelectorAll('.step2-control-pending')
        .forEach(r => r.classList.remove('step2-control-pending'));
      updatePendingBadge();
    }
    if (msg) {
      msgInput.value = '';
      appendMessage({ role: 'user', text: msg });
    } else if (adjustmentsSnapshot) {
      // Adjustments-only turn (no NL message) — surface the staged
      // params as the user-side bubble.
      const summary = Object.entries(adjustmentsSnapshot)
        .map(([k, v]) => `${k} = ${typeof v === 'string' ? v : JSON.stringify(v)}`)
        .join(', ');
      appendMessage({ role: 'user', text: `Adjust: ${summary}` });
    }
    const requestId = ++latestRefineRequestId;
    setSending(true);
    try {
      const payload = {};
      if (msg) payload.message = msg;
      if (adjustmentsSnapshot) payload.adjustments = adjustmentsSnapshot;
      const result = await postRefine(payload);
      if (requestId !== latestRefineRequestId) return;
      if (result) {
        handleAiResult(result);
      } else if (adjustmentsSnapshot) {
        // POST failed; restore pending so the user can retry.
        Object.assign(pendingAdjustments, adjustmentsSnapshot);
        controlsList?.querySelectorAll('.step2-control-input').forEach(inp => {
          if (inp.dataset.param in pendingAdjustments) {
            inp.closest('.step2-control')?.classList.add('step2-control-pending');
          }
        });
        updatePendingBadge();
      }
    } finally {
      if (requestId === latestRefineRequestId) setSending(false);
    }
  });

  // Cmd/Ctrl+Enter shortcut.
  msgInput.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  function setSending(on) {
    const send = form.querySelector('.step2-send');
    if (send) {
      send.disabled = on;
      send.textContent = on ? 'Sending…' : 'Send →';
    }
  }

  async function postRefine(payload) {
    // Phase 3: rename rcparams → adjustments (server still accepts
    // legacy 'rcparams' as a deprecation shim, but the new name is
    // what's documented).
    const out = { ...payload };
    if (out.rcparams && !out.adjustments) {
      out.adjustments = out.rcparams;
      delete out.rcparams;
    }
    const body = JSON.stringify({
      run: runName,
      baseline_iters: baselineIters,
      ...out,
    });
    try {
      const r = await fetch('/api/refine', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => '');
        let msg = `refine failed: HTTP ${r.status}`;
        try {
          const parsed = JSON.parse(detail);
          if (parsed && parsed.error && parsed.error.message) {
            msg = `refine failed: ${parsed.error.code || 'error'} — ${parsed.error.message}`;
          } else if (detail) {
            msg += ` — ${detail.slice(0, 200)}`;
          }
        } catch (_e) {
          if (detail) msg += ` — ${detail.slice(0, 200)}`;
        }
        appendMessage({ role: 'system', text: msg });
        return null;
      }
      return await r.json();
    } catch (e) {
      appendMessage({
        role: 'system',
        text: `refine network error: ${e.message || e}`,
      });
      return null;
    }
  }

  // Cancel button — when a refine is in flight, POST cancel with the
  // current set_id. The runner SIGTERMs the subprocess; SSE will
  // surface a turn_end:cancelled event.
  const cancelBtn = document.getElementById('step2-cancel');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
      if (!setId) return;
      try {
        await fetch(
          `/api/runs/${encodeURIComponent(runName)}/cancel?slot=refine&set_id=${setId}`,
          { method: 'POST' },
        );
        appendMessage({ role: 'system', text: 'cancel requested…' });
      } catch (_e) {}
    });
  }

  function handleAiResult(result) {
    const review = result.review || '';
    const delta = result.rcparams_delta || {};
    const imageUrl = result.image_url || null;
    // Dedupe: SSE refine_complete may have appended the same message
    // already (the live event beat the POST response). If the last AI
    // message in state already carries this image_url, just update
    // controls + working image, don't duplicate the chat bubble.
    const lastAi = [...state.messages].reverse().find(m => m.role === 'ai');
    if (!(lastAi && lastAi.image_url === imageUrl)) {
      clearLiveBubble();
      appendMessage({ role: 'ai', text: '', review, delta, image_url: imageUrl });
    }
    // Merge delta into accumulated state.
    let changed = false;
    for (const [k, v] of Object.entries(delta)) {
      if (state.deltas[k] !== v) {
        state.deltas[k] = v;
        changed = true;
      }
      ensureControl(k, v);
    }
    if (changed) saveState();
    if (imageUrl && currentImg) {
      const img = currentImg.querySelector('img');
      if (img && img.dataset.currentUrl !== imageUrl) {
        img.dataset.currentUrl = imageUrl;
        img.src = imageUrl;
      }
      currentImg.hidden = false;
    }
  }
}


/* ─────────── Live polling + helpbar ─────────── */

let lastSig = null;
// Build the per-run state-poll URL absolutely. Workspace mode serves
// the trajectory at `/r/<name>` (no trailing slash), so a relative
// `_state.json` would resolve to `/r/_state.json` and 404. Single-run
// mode serves the page at `/figcopy_serve.html`; relative resolves to
// `/_state.json` which the single-run handler matches.
const STATE_POLL_URL = (() => {
  const name = runNameFromUrl();
  return name ? `/r/${encodeURIComponent(name)}/_state.json` : '/_state.json';
})();
const POLL_TERMINAL_STATES = new Set(['shipped', 'failed', 'cancelled']);
let pollIntervalId = null;
async function pollState() {
  try {
    const r = await fetch(STATE_POLL_URL + '?_=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) return;
    const j = await r.json();
    const sig = JSON.stringify(j);
    if (lastSig === null) {
      lastSig = sig;
    } else if (lastSig !== sig) {
      const banner = document.querySelector('.status-text');
      if (banner) banner.textContent = 'new iter detected — reloading…';
      setTimeout(() => location.reload(), 400);
      return;
    }
    // Stop polling once the run reaches a terminal state — there's
    // nothing more for the page to react to. Saves an HTTP round-trip
    // every 3s for every long-lived browser tab on a finished run.
    if (POLL_TERMINAL_STATES.has(j && j.status) && pollIntervalId !== null) {
      clearInterval(pollIntervalId);
      pollIntervalId = null;
      const dot = document.querySelector('.status .dot');
      if (dot) dot.style.animation = 'none';
    }
  } catch (_err) {
    // Silently ignore — server might be momentarily unreachable.
  }
}
const statusEl = document.getElementById('status');
if (statusEl) statusEl.classList.add('live');
// Don't poll-and-reload on Step 2 (chat view): the user is mid-chat,
// reloading would discard their typing + the live SSE bubble. SSE
// already delivers refine_complete events for the right-panel image
// swap. Also skip when status is already terminal at page load.
const isStep2 = location.search.includes('step=2');
const initialStatus = document.getElementById('status')?.dataset?.status || '';
const startPolling = !isStep2 && !POLL_TERMINAL_STATES.has(initialStatus);
if (startPolling) {
  pollIntervalId = setInterval(pollState, 3000);
}

// Help bar: show briefly on load, hide on first interaction.
const helpbar = document.getElementById('helpbar');
if (helpbar) {
  helpbar.classList.add('on');
  let dismissed = false;
  const dismiss = () => {
    if (!dismissed) { helpbar.classList.remove('on'); dismissed = true; }
  };
  setTimeout(dismiss, 6000);
  document.addEventListener('click', dismiss, { once: true });
  document.addEventListener('keydown', dismiss, { once: true });
}
