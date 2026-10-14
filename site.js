/* Progressive enhancement: native links and disclosures work without JavaScript. */
(() => {
  'use strict';

  const questions = [...document.querySelectorAll('[data-research-question]')];
  const studies = [...document.querySelectorAll('.research-entry')];

  function emphasize(question) {
    const related = new Set((question?.dataset.relatedWorks || '').split(' ').filter(Boolean));
    questions.forEach(link => {
      if (link === question) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
    studies.forEach(study => study.classList.toggle('is-related', related.has(study.id.replace('research-', ''))));
  }

  questions.forEach(question => question.addEventListener('click', () => emphasize(question)));
  window.addEventListener('hashchange', () => {
    const active = questions.find(question => question.getAttribute('aria-current') === 'true');
    if (active?.hash === location.hash) return;
    emphasize(questions.find(question => question.hash === location.hash));
  });
  if (location.hash) emphasize(questions.find(question => question.hash === location.hash));

  if (typeof HTMLDialogElement === 'undefined') return;
  let dialog;
  let image;
  let title;
  let previousFocus;

  function createFigureDialog() {
    dialog = document.createElement('dialog');
    dialog.className = 'figure-dialog';
    dialog.setAttribute('aria-labelledby', 'figure-dialog-title');
    const header = document.createElement('div');
    header.className = 'figure-dialog-heading';
    title = document.createElement('p');
    title.id = 'figure-dialog-title';
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = 'Close ×';
    close.addEventListener('click', () => dialog.close());
    image = document.createElement('img');
    header.append(title, close);
    dialog.append(header, image);
    dialog.addEventListener('close', () => previousFocus?.focus());
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
    document.body.append(dialog);
  }

  document.querySelectorAll('.figure-link').forEach(link => {
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const preview = link.querySelector('img');
      if (!preview) return;
      event.preventDefault();
      if (!dialog) createFigureDialog();
      previousFocus = link;
      title.textContent = 'Complete paper figure';
      image.alt = preview.alt;
      image.src = link.href;
      dialog.showModal();
    });
  });
})();
