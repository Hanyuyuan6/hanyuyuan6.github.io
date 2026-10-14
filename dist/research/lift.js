/* The evidence table is static HTML from profile.json. No values are simulated. */
(() => {
  'use strict';

  const form = document.querySelector('.comparison-controls');
  const groups = [...document.querySelectorAll('.comparison-table tbody[data-dataset]')];
  const status = document.getElementById('selection-status');
  if (form && groups.length && status) {
    function filterComparisons() {
      const dataset = form.querySelector('input[name="dataset"]:checked')?.value || 'all';
      const condition = form.querySelector('input[name="condition"]:checked')?.value || 'both';
      let count = 0;
      groups.forEach(group => {
        group.hidden = dataset !== 'all' && group.dataset.dataset !== dataset;
        group.querySelectorAll('.result-row').forEach(row => {
          row.hidden = condition !== 'both' && row.dataset.condition !== condition;
          if (!group.hidden && !row.hidden) count += 1;
        });
      });
      const conditionLabel = condition === 'both' ? 'Clean and 20 dB' : condition === 'clean' ? 'Clean' : '20 dB';
      status.textContent = `${dataset === 'all' ? 'All three datasets' : dataset} · ${conditionLabel} · ${count} reported comparison${count === 1 ? '' : 's'}.`;
    }
    form.querySelectorAll('fieldset').forEach(fieldset => { fieldset.disabled = false; });
    form.addEventListener('change', filterComparisons);
    filterComparisons();
  }

  const noise = document.getElementById('synthetic-noise');
  const wave = document.getElementById('synthetic-wave');
  const waveStatus = document.getElementById('synthetic-wave-status');
  if (noise && wave && waveStatus) {
    function drawTeachingWave() {
      const values = Array.from({ length: 64 }, (_, i) =>
        0.51 + 0.21 * Math.sin(i * 0.33) + 0.10 * Math.cos(i * 0.91) +
        (noise.checked ? 0.10 * Math.sin(i * 17.37) + 0.055 * Math.cos(i * 4.19) : 0)
      );
      wave.setAttribute('d', values.map((value, i) =>
        `${i ? 'L' : 'M'}${(8 + i * 704 / 63).toFixed(2)} ${(92 - value * 73).toFixed(2)}`
      ).join(' '));
      waveStatus.textContent = noise.checked ? 'Synthetic input with added perturbation.' : 'Synthetic input without added perturbation.';
    }
    noise.disabled = false;
    noise.addEventListener('change', drawTeachingWave);
    drawTeachingWave();
  }

  const image = document.getElementById('lift-detail-image');
  const viewport = document.getElementById('lift-figure-viewport');
  const region = document.getElementById('lift-figure-region');
  const figureStatus = document.getElementById('lift-figure-status');
  const partTitle = document.getElementById('lift-part-title');
  const partDescription = document.getElementById('lift-part-description');
  if (image && viewport && region && figureStatus && partTitle && partDescription) {
    const parts = {
      all: { title: 'The complete architecture', description: 'Measurements, the spatial representation and training appear together in the original figure.' },
      acquisition: { title: 'Measurements and temporal encoding', description: 'Structured illumination produces a one-dimensional sequence. Temporal encoding extracts features from those measurements.', box: [0, 0, 0.33, 0.57], x: 0, y: 0 },
      lift: { title: 'A content-adaptive spatial lift', description: 'Cross-attention maps the encoded measurement sequence into two-dimensional task features.', box: [0.33, 0, 0.43, 0.58], x: 0.31, y: 0 },
      tpls: { title: 'Task-prioritized loss scheduling', description: 'Training gradually shifts priority from auxiliary reconstruction towards the segmentation task.', box: [0.31, 0.60, 0.31, 0.34], x: 0.30, y: 0.57 }
    };
    document.querySelectorAll('[data-figure-part]').forEach(button => {
      button.disabled = false;
      button.addEventListener('click', () => {
        const part = parts[button.dataset.figurePart];
        if (!part) return;
        document.querySelectorAll('[data-figure-part]').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
        partTitle.textContent = part.title;
        partDescription.textContent = part.description;
        region.hidden = !part.box;
        viewport.classList.toggle('is-zoomed', Boolean(part.box));
        image.style.width = part.box ? `${Math.max(1200, viewport.clientWidth * 1.75)}px` : '100%';
        if (part.box) {
          const [x, y, width, height] = part.box;
          Object.assign(region.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` });
        }
        requestAnimationFrame(() => {
          viewport.scrollLeft = part.box ? part.x * image.clientWidth : 0;
          viewport.scrollTop = part.box ? part.y * image.clientHeight : 0;
        });
        figureStatus.textContent = part.box ? 'Original figure enlarged. Scroll to explore; choose Complete figure to reset.' : 'Complete original figure. Select a module to locate it.';
      });
    });
  }
})();
